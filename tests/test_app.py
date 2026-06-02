from types import SimpleNamespace
from unittest.mock import Mock

from kuredhorn.app import (
    check_nodes_for_annotation,
    delete_longhorn_instance_manager,
    evict_longhorn_nodes,
    main,
    remove_longhorn_eviction,
)


def make_node(
    name: str = "node-a",
    annotations: dict | None = None,
    unschedulable: bool = False,
    metadata: bool = True,
    spec: bool = True,
):
    node_metadata = None
    node_spec = None

    if metadata:
        node_metadata = SimpleNamespace(name=name, annotations=annotations)
    if spec:
        node_spec = SimpleNamespace(unschedulable=unschedulable)

    return SimpleNamespace(metadata=node_metadata, spec=node_spec)


def make_pod(
    name: str,
    node_name: str,
    component: str = "instance-manager",
):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            labels={"longhorn.io/component": component},
        ),
        spec=SimpleNamespace(node_name=node_name),
    )


def test_check_nodes_for_annotation_returns_only_cordoned_annotated_nodes():
    annotation_key = "weave.works/kured-reboot-in-progress"
    nodes = [
        make_node(
            name="node-a",
            annotations={annotation_key: "true"},
            unschedulable=True,
        ),
        make_node(
            name="node-b",
            annotations={annotation_key: "true"},
            unschedulable=False,
        ),
        make_node(name="node-c", annotations={}, unschedulable=True),
        make_node(name="node-d", annotations=None, unschedulable=True),
        make_node(name="node-e", annotations={annotation_key: "true"}, spec=False),
        make_node(metadata=False),
    ]

    matching_nodes = check_nodes_for_annotation(nodes, annotation_key)

    assert [node.metadata.name for node in matching_nodes] == ["node-a"]


def test_evict_longhorn_nodes_patches_matching_nodes():
    annotation_key = "drain-annotation"
    custom_client = Mock()
    custom_client.list_namespaced_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "node-a", "annotations": {}},
                "spec": {"allowScheduling": True, "evictionRequested": False},
            },
            {
                "metadata": {"name": "node-b", "annotations": {}},
                "spec": {"allowScheduling": True, "evictionRequested": False},
            },
        ]
    }

    evict_longhorn_nodes(
        [make_node(name="node-a", annotations={annotation_key: "true"})],
        custom_client,
        "longhorn-system",
        annotation_key,
    )

    custom_client.patch_namespaced_custom_object.assert_called_once()
    patched_node = custom_client.patch_namespaced_custom_object.call_args.args[-1]
    assert patched_node["metadata"]["name"] == "node-a"
    assert patched_node["metadata"]["annotations"][annotation_key] == "true"
    assert patched_node["spec"]["allowScheduling"] is False
    assert patched_node["spec"]["evictionRequested"] is True


def test_evict_longhorn_nodes_skips_already_draining_nodes():
    annotation_key = "drain-annotation"
    custom_client = Mock()
    custom_client.list_namespaced_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "node-a", "annotations": {annotation_key: "true"}},
                "spec": {"allowScheduling": True, "evictionRequested": False},
            }
        ]
    }

    evict_longhorn_nodes(
        [make_node(name="node-a", annotations={annotation_key: "true"})],
        custom_client,
        "longhorn-system",
        annotation_key,
    )

    custom_client.patch_namespaced_custom_object.assert_not_called()


def test_remove_longhorn_eviction_reenables_node_when_uncordoned():
    annotation_key = "drain-annotation"
    api_client = Mock()
    custom_client = Mock()
    custom_client.list_namespaced_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "node-a", "annotations": {annotation_key: "true"}},
                "spec": {"allowScheduling": False, "evictionRequested": True},
            }
        ]
    }
    api_client.read_node.return_value = make_node(
        name="node-a",
        annotations={annotation_key: "true"},
        unschedulable=False,
    )

    remove_longhorn_eviction(
        api_client,
        custom_client,
        "longhorn-system",
        annotation_key,
    )

    custom_client.patch_namespaced_custom_object.assert_called_once()
    patched_node = custom_client.patch_namespaced_custom_object.call_args.args[-1]
    assert patched_node["spec"]["allowScheduling"] is True
    assert patched_node["spec"]["evictionRequested"] is False
    assert patched_node["metadata"]["annotations"][annotation_key] == "false"


def test_delete_longhorn_instance_manager_skips_when_replicas_remain():
    annotation_key = "drain-annotation"
    custom_client = Mock()
    api_client = Mock()
    custom_client.list_namespaced_custom_object.side_effect = [
        {
            "items": [
                {
                    "metadata": {
                        "name": "node-a",
                        "annotations": {annotation_key: "true"},
                    },
                    "spec": {},
                }
            ]
        },
        {"items": [{"spec": {"nodeID": "node-a"}}]},
    ]

    delete_longhorn_instance_manager(
        custom_client,
        api_client,
        "longhorn-system",
        annotation_key,
        remove_replicas=True,
    )

    api_client.list_namespaced_pod.assert_not_called()
    api_client.delete_namespaced_pod.assert_not_called()


def test_delete_longhorn_instance_manager_deletes_instance_manager_pod():
    annotation_key = "drain-annotation"
    custom_client = Mock()
    api_client = Mock()
    custom_client.list_namespaced_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "node-a", "annotations": {annotation_key: "true"}},
                "spec": {},
            }
        ]
    }
    api_client.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            make_pod("instance-manager-a", "node-a"),
            make_pod("other-pod", "node-b"),
        ]
    )

    delete_longhorn_instance_manager(
        custom_client,
        api_client,
        "longhorn-system",
        annotation_key,
    )

    api_client.delete_namespaced_pod.assert_called_once_with(
        "instance-manager-a",
        "longhorn-system",
    )


def test_main_runs_single_testing_iteration(monkeypatch):
    api_client = Mock()
    api_client.list_node.return_value = SimpleNamespace(items=[])
    custom_client = Mock()

    core_api_factory = Mock(return_value=api_client)
    custom_api_factory = Mock(return_value=custom_client)
    load_kube_config = Mock()
    evict = Mock()
    remove = Mock()
    delete = Mock()

    monkeypatch.setattr("kuredhorn.app.config.load_kube_config", load_kube_config)
    monkeypatch.setattr("kuredhorn.app.client.CoreV1Api", core_api_factory)
    monkeypatch.setattr("kuredhorn.app.client.CustomObjectsApi", custom_api_factory)
    monkeypatch.setattr("kuredhorn.app.evict_longhorn_nodes", evict)
    monkeypatch.setattr("kuredhorn.app.remove_longhorn_eviction", remove)
    monkeypatch.setattr("kuredhorn.app.delete_longhorn_instance_manager", delete)

    main(testing=True, not_in_cluster=True)

    load_kube_config.assert_called_once()
    core_api_factory.assert_called_once()
    custom_api_factory.assert_called_once()
    evict.assert_called_once()
    remove.assert_called_once_with(
        api_client,
        custom_client,
        "longhorn-system",
        "weave.works/kured-reboot-in-progress",
    )
    delete.assert_called_once_with(
        custom_client,
        api_client,
        "longhorn-system",
        "weave.works/kured-reboot-in-progress",
        False,
    )
