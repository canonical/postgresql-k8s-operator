#!/bin/bash

set -Eeuo pipefail

chaos_mesh_ns=$1
chaos_mesh_version="2.4.1"

if [ -z "${chaos_mesh_ns}" ]; then
	echo "Error: missing mandatory argument. Aborting" >&2
	exit 1
fi

deploy_chaos_mesh() {
	echo "adding chaos-mesh helm repo"
	sudo k8s helm repo add chaos-mesh https://charts.chaos-mesh.org

	echo "installing chaos-mesh"
        sudo k8s helm install chaos-mesh chaos-mesh/chaos-mesh \
          --namespace="${chaos_mesh_ns}" \
          --set chaosDaemon.runtime=containerd \
          --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
          --set dashboard.create=false \
          --version "${chaos_mesh_version}" \
          --set clusterScoped=false \
          --set controllerManager.targetNamespace="${chaos_mesh_ns}"
	sleep 10
}

echo "namespace=${chaos_mesh_ns}"
deploy_chaos_mesh
