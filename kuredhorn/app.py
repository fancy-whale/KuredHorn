import logging
from kubernetes import client, config
import time
import os
from kubernetes.client.models.v1_node import V1Node
from typing import Any, Union, List
from kubernetes.client.api.core_v1_api import CoreV1Api
from kubernetes.client.api.custom_objects_api import CustomObjectsApi

# Create a logger instance
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a console handler and set its log level
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create a formatter and add it to the console handler
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)

# Add the console handler to the logger
logger.addHandler(console_handler)

def check_nodes_for_annotation(
    all_nodes: List[V1Node], annotation_key: str
) -> List[V1Node]:
    # Get the list of nodes

    # Create an empty list to store nodes with the desired annotation
    nodes_with_annotation = []

    # Iterate over each node
    for node in all_nodes:
        # Check if the annotation exists on the node
        if (
            annotation_key in node.metadata.annotations
            and node.metadata.annotations[annotation_key]
        ):
            # Check if node is cordoned
            if node.spec.unschedulable:
                logger.info(f"Node {node.metadata.name} is already cordoned")
                # Add the node to the list of nodes with the desired annotation
                nodes_with_annotation.append(node)

    # Return the list of nodes with the desired annotation
    return nodes_with_annotation


def evict_longhorn_nodes(nodes_with_annotation: List[Union[V1Node, Any]], custom_client: CustomObjectsApi, longhorn_namespace: str, annotation_key: str) -> None:
    # Try to set the longhorn node to evacuate
    all_longhorn_nodes = custom_client.list_namespaced_custom_object(
        "longhorn.io", "v1beta2", longhorn_namespace, "nodes"
    ).get("items")

    logger.info(
        f"all_longhorn_nodes: {[node.get('metadata').get('name') for node in all_longhorn_nodes]}"
    )

    for node in [
        node
        for node in all_longhorn_nodes
        if node.get("metadata").get("name")
        in [node_classic.metadata.name for node_classic in nodes_with_annotation]
    ]:
        try:
            # if the kured-reboot-in-progress annotation is already set, skip the node
            if (
                annotation_key in node.get("metadata").get("annotations", {}).get(annotation_key, 'false') == 'true'
            ):
                logger.info(f"Node {node.get('metadata').get('name')} already being drained")
                continue

            # change the response object's spec to be unschedulable and to evacuate the node
            node["spec"]["allowScheduling"] = False
            node["spec"]["evictionRequested"] = True

            # add annotation to the node object to ensure we don't try to evict the node again
            if "annotations" not in node["metadata"]:
                node["metadata"]["annotations"] = {}
            node["metadata"]["annotations"][annotation_key] = "true"

            # patch the node object to set the node to evacuate
            custom_client.patch_namespaced_custom_object(
                "longhorn.io",
                "v1beta2",
                longhorn_namespace,
                "nodes",
                node.get("metadata").get("name"),
                node,
            )

            logger.info(f"Evicted node {node.get('metadata').get('name')}")
        except Exception as e:
            logger.error(
                f"An error occurred while evicting node {node.get('metadata').get('name')}: {str(e)}"
            )
            raise e


def remove_longhorn_eviction(api_client: CoreV1Api, custom_client: CustomObjectsApi, longhorn_namespace: str, annotation_key: str) -> None:
    all_longhorn_nodes = custom_client.list_namespaced_custom_object(
        "longhorn.io", "v1beta2", longhorn_namespace, "nodes"
    ).get("items")

    logger.info(
        f"all_longhorn_nodes: {[node.get('metadata').get('name') for node in all_longhorn_nodes]}"
    )

    for node in all_longhorn_nodes:
        # if the kured-reboot-in-progress annotation is set, check if the node has been drained and rebooted
        annotation = node.get("metadata").get("annotations", {}).get(annotation_key, None)
        if annotation == "true":
            # Longhorn node has been drained, checking if the node is still cordoned through the kubernetes API
            try:
                node_classic = api_client.read_node(node.get("metadata").get("name"))
                if node_classic.spec.unschedulable:
                    logger.info(f"Node {node.get('metadata').get('name')} is still cordoned")
                    continue
            except Exception as e:
                logger.error(
                    f"An error occurred while checking node {node.get('metadata').get('name')}: {str(e)}"
                )
                raise e
            # if the node is no longer cordoned, remove the annotation and allow scheduling
            try:
                logger.info(f"Node {node.get('metadata').get('name')} is no longer cordoned")
                node["spec"]["allowScheduling"] = True
                node["spec"]["evictionRequested"] = False
                # remove the annotation from the node object
                if "annotations" in node["metadata"]:
                    if annotation_key in node["metadata"]["annotations"]:
                        logger.info(f"Setting annotation {annotation_key} on node {node['metadata']['name']} to false")
                        node["metadata"]["annotations"][annotation_key] = 'false'

                # patch the node object to set the node to evacuate
                custom_client.patch_namespaced_custom_object(
                    "longhorn.io",
                    "v1beta2",
                    longhorn_namespace,
                    "nodes",
                    node.get("metadata").get("name"),
                    node,
                )

                logger.info(f"Removed eviction status from node {node.get('metadata').get('name')}")
            except Exception as e:
                logger.error(
                    f"An error occurred while removing eviction from node {node.get('metadata').get('name')}: {str(e)}"
                )
                raise e
        else:
            continue


def delete_longhorn_instance_manager(custom_client: CustomObjectsApi, api_client: CoreV1Api, longhorn_namespace: str, annotation_key: str) -> None:
    # Get the list of longhorn nodes
    all_longhorn_nodes = custom_client.list_namespaced_custom_object(
        "longhorn.io", "v1beta2", longhorn_namespace, "nodes"
    ).get("items")

    # Get the list of longhorn volumes
    all_longhorn_replicas = custom_client.list_namespaced_custom_object(
        "longhorn.io", "v1beta1", longhorn_namespace, "replicas"
    ).get("items")

    # check if there are any volumes on the node that is currently being drained
    for node in all_longhorn_nodes:
        if node.get('metadata').get('annotations', {}).get(annotation_key, None) != 'true':
            continue
    
        node_name = node.get('metadata').get('name')
        volumes_on_node = [
            volume
            for volume in all_longhorn_replicas
            if volume.get('spec').get('nodeID') == node_name
        ]
        if len(volumes_on_node) != 0:
            logger.info(f"Node {node_name} has replicas, skipping deletion of instance manager until replicas are moved")
            continue
    
        logger.info(f"Node {node_name} has no replicas, deleting instance manager")
        # get pods on the longhorn namespace and check if the instance manager is running on the node
        pods = api_client.list_namespaced_pod(longhorn_namespace).items
        instance_manager_pod = [
            pod
            for pod in pods
            if pod.metadata.labels.get('longhorn.io/component') == 'instance-manager'
            and pod.spec.node_name == node_name
        ]
        if len(instance_manager_pod) == 0:
            logger.info(f"Instance manager not found on node {node_name}")
            continue
        try:
            for pod in instance_manager_pod:
                logger.info(f"Deleting instance manager pod {pod.metadata.name}")
                api_client.delete_namespaced_pod(pod.metadata.name, longhorn_namespace)
        except Exception as e:
            logger.error(
                f"An error occurred while deleting instance manager pod {pod.metadata.name}: {str(e)}"
            )
            raise e

def main(testing: bool = False, not_in_cluster: bool = False) -> None:
    try:
        # Load the Kubernetes configuration
        if not not_in_cluster:
            logger.info("Loading in-cluster configuration")
            config.load_incluster_config()
        else:
            logger.info("Loading out-of-cluster configuration")
            config.load_kube_config()
    except Exception as e:
        logger.error(f"An error occurred while loading the Kubernetes configuration: {str(e)}")
        raise e

    # Create a Kubernetes API client
    api_client = client.CoreV1Api()
    custom_client = client.CustomObjectsApi()

    # Annotation to check for
    annotation_key = "weave.works/kured-reboot-in-progress"
    longhorn_namespace = os.environ.get("LONGHORN_NAMESPACE", "longhorn-system")

    sleep_duration = int(os.environ.get("SLEEP_DURATION", 60))

    # Loop to check nodes for the annotation
    not_done = True
    while True and not_done:
        try:
            # Get the list of nodes
            all_nodes = api_client.list_node().items

            # Get the list of nodes
            nodes_with_annotation = check_nodes_for_annotation(
                all_nodes, annotation_key
            )
            logger.info(
                f"Nodes with annotation: {[node.metadata.name for node in nodes_with_annotation]}"
            )

            # Do the needful with the nodes that have the desired annotation
            evict_longhorn_nodes(nodes_with_annotation, custom_client, longhorn_namespace, annotation_key)

            # remove longhorn eviction on nodes that have already been drained and rebooted
            remove_longhorn_eviction(api_client, custom_client, longhorn_namespace, annotation_key)

            # Delete longhorn instance manager from the node if there are no more volumes there
            delete_longhorn_instance_manager(custom_client, api_client, longhorn_namespace, annotation_key)

            if testing:
                logger.info("Exiting as testing is enabled")
                not_done = False
            # Wait for some time before checking again
            else:
                logger.info(f"Sleeping for {sleep_duration} seconds")
                time.sleep(float(sleep_duration))  # Adjust the sleep duration as needed
        except KeyboardInterrupt:
            logger.info("Exiting")
            not_done = False
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
            raise e


def run() -> None:
    # get the testing environment variable
    testing = os.environ.get("TESTING", "False").lower() == "true"
    not_in_cluster = os.environ.get("NOT_IN_CLUSTER", "False").lower() == "true"
    logger.info(f"testing: {testing}")
    main(testing, not_in_cluster)


if __name__ == "__main__":
    run()
