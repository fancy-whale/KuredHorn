# LongHorn KuReD fix

This is not supposed to be a long term fix, but it should work for now.

## TL;DR

If you have flux installed with helm-releases and you are using LongHorn, you might have a problem with the KuReD feature. This is a fix for that.

```bash
kubectl apply -f https://raw.githubusercontent.com/fancy-whale/KuredHorn/main/helmrelease.yaml
```

if you don't have Flux and helm-releases, you can use the FancyWhale helm chart to install this fix. First you need to add the necessary configuration into a values.yaml file:

```yaml
image: # The image to use for the container
  pullPolicy: IfNotPresent
  repo: ghcr.io/fancy-whale/kuredhorn # The repository to pull the image from
  tag: latest # The tag of the image to pull - I should fix this soon
replicas: 1 # The number of replicas to run
env:
  # NOT_IN_CLUSTER: "true" # If you are not running in a cluster, set this to true, it will allow you to run this in a non-cluster environment
  ## You can run this image in a non-cluster environment like this:
  ## docker run -v ~/.kube/config:/root/.kube/config -it -e NOT_IN_CLUSTER=true ghcr.io/fancy-whale/kuredhorn:latest
  # TESTING: "true" # If you are testing, set this to true, it will allow you to run this only once, without the need to get stuck in a loop
  SLEEP_DURATION: "60" # The number of seconds to sleep between checks. Default is 60
  LONGHORN_NAMESPACE: "longhorn-system" # The namespace where LongHorn is installed. Default is longhorn-system
  REMOVE_REPLICAS: "true" # Whether to remove replicas from the node. Default is False.
serviceAccount: # The service account the pod should run as
  enabled: true # Whether to create a service account
  clusterRole: # The cluster role to bind to the service account
    rules: # The rules to bind to the service account
      - apiGroups: # This rule allows the service account to list and delete pods for deleting the longhorn manager pods
          - ""
        resources:
          - pods
        verbs:
          - get
          - list
          - delete
      - apiGroups: # This rule allows the service account to list and patch the longhorn nodes and replicas
          - "longhorn.io"
        resources:
          - nodes
          - replicas
        verbs:
          - get
          - list
          - watch
          - patch
      - apiGroups: # This rule allows the service account to list the nodes in the cluster - to check for the kured annotation
          - ""
        resources:
          - nodes
        verbs:
          - get
          - list
```

Now that you have the configuration, you can install the chart:

```bash
    helm repo add fancywhale https://gitlab.fancywhale.ca/api/v4/projects/104/packages/helm/stable
    helm install kuredhorn fancywhale/main -f values.yaml
```

## Introduction

This is a fix for an issue I've had with LongHorn for quite a while: LongHorn's PDB for the manager pod won't release as expected. The idea here is simple:

1. Check which pod is annotated by KuReD and cordoned.
2. Mark the LongHorn node as unschedulable and mark for eviction.
3. Wait for the LongHorn node not to have any replicas running in it.
4. Delete the LongHorn manager pods for this node that are stuck by the PDB.
5. Wait until KuReD does its job and uncordons the node.
6. Mark the LongHorn node as schedulable again, remove eviction mark.
