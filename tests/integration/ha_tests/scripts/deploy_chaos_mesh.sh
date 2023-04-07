#!/bin/bash

chaos_mesh_ns=$1
chaos_mesh_version="2.4.1"

if [ -z "${chaos_mesh_ns}" ]; then
	exit 1
fi

deploy_chaos_mesh() {
	if [ "$(helm repo list | grep 'chaos-mesh' | wc -l)" != "1" ]; then
		echo "adding chaos-mesh helm repo"
		helm repo add chaos-mesh https://charts.chaos-mesh.org
	fi

	echo "installing chaos-mesh"
    helm install chaos-mesh chaos-mesh/chaos-mesh --namespace=${chaos_mesh_ns} --set chaosDaemon.runtime=containerd --set chaosDaemon.socketPath=/var/snap/microk8s/common/run/containerd.sock --set dashboard.create=false --version ${chaos_mesh_version} --set clusterScoped=false --set controllerManager.targetNamespace=${chaos_mesh_ns}
	sleep 10
}

echo "namespace=${chaos_mesh_ns}"
deploy_chaos_mesh
