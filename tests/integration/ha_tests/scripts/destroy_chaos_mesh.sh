#!/bin/bash

chaos_mesh_ns=$1

if [ -z "${chaos_mesh_ns}" ]; then
	exit 1
fi

destroy_chaos_mesh() {
	echo "deleting api-resources"
	for i in $(kubectl api-resources | grep chaos-mesh | awk '{print $1}'); do timeout 30 kubectl delete ${i} --all --all-namespaces || :; done

	if [ "$(kubectl -n ${chaos_mesh_ns} get mutatingwebhookconfiguration | grep 'choas-mesh-mutation' | wc -l)" = "1" ]; then
		timeout 30 kubectl -n ${chaos_mesh_ns} delete mutatingwebhookconfiguration chaos-mesh-mutation || :
	fi

	if [ "$(kubectl -n ${chaos_mesh_ns} get validatingwebhookconfiguration | grep 'chaos-mesh-validation' | wc -l)" = "1" ]; then
		timeout 30 kubectl -n ${chaos_mesh_ns} delete validatingwebhookconfiguration chaos-mesh-validation || :
	fi

	if [ "$(kubectl -n ${chaos_mesh_ns}) get validatingwebhookconfiguration | grep 'chaos-mesh-validate-auth' | wc -l)" = "1" ]; then
		timeout 30 kubectl -n ${chaos_mesh_ns} delete validatingwebhookconfiguration chaos-mesh-validate-auth || :
	fi

	if [ "$(kubectl get clusterrolebinding | grep 'chaos-mesh' | awk '{print $1}' | wc -l)" != "0" ]; then
		echo "deleting clusterrolebindings"
		timeout 30 kubectl delete clusterrolebinding $(kubectl get clusterrolebinding | grep 'chaos-mesh' | awk '{print $1}') || :
	fi

	if [ "$(kubectl get clusterrole | grep 'chaos-mesh' | awk '{print $1}' | wc -l)" != "0" ]; then
		echo "deleting clusterroles"
		timeout 30 kubectl delete clusterrole $(kubectl get clusterrole | grep 'chaos-mesh' | awk '{print $1}') || :
	fi

	if [ "$(kubectl get crd | grep 'chaos-mesh.org' | awk '{print $1}' | wc -l)" != "0" ]; then
		echo "deleting crds"
		timeout 30 kubectl delete crd $(kubectl get crd | grep 'chaos-mesh.org' | awk '{print $1}') || :
	fi

	if [ -n "$chaos_mesh_ns" ] && [ "$(helm repo list --namespace $chaos_mesh_ns | grep 'chaos-mesh' | wc -l)" = "1" ]; then
		echo "uninstalling chaos-mesh helm repo"
		helm uninstall chaos-mesh --namespace ${chaos_mesh_ns} || :
	fi
}

echo "Destroying chaos mesh in ${chaos_mesh_ns}"
destroy_chaos_mesh
