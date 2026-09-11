#!/bin/bash

set -Eeuo pipefail

chaos_mesh_ns=$1

if [ -z "${chaos_mesh_ns}" ]; then
	echo "Error: missing mandatory argument. Aborting" >&2
	exit 1
fi

destroy_chaos_mesh() {
	echo "deleting api-resources"
	for i in $(sudo k8s kubectl api-resources | awk '/chaos-mesh/ {print $1}'); do
	    timeout 30 sudo k8s kubectl delete "${i}" --all --all-namespaces || true
	done

	if sudo k8s kubectl get mutatingwebhookconfiguration | grep -q 'chaos-mesh-mutation'; then
		timeout 30 sudo k8s kubectl delete mutatingwebhookconfiguration chaos-mesh-mutation || true
	fi

	if sudo k8s kubectl get validatingwebhookconfiguration | grep -q 'chaos-mesh-validation-auth'; then
		timeout 30 sudo k8s kubectl delete validatingwebhookconfiguration chaos-mesh-validation-auth || true
	fi

	if sudo k8s kubectl get validatingwebhookconfiguration | grep -q 'chaos-mesh-validation'; then
		timeout 30 sudo k8s kubectl delete validatingwebhookconfiguration chaos-mesh-validation || true
	fi

	if sudo k8s kubectl get clusterrolebinding | grep -q 'chaos-mesh'; then
		echo "deleting clusterrolebindings"
		readarray -t args < <(sudo k8s kubectl get clusterrolebinding | awk '/chaos-mesh/ {print $1}')
		timeout 30 sudo k8s kubectl delete clusterrolebinding "${args[@]}" || true
	fi

	if sudo k8s kubectl get clusterrole | grep -q 'chaos-mesh'; then
		echo "deleting clusterroles"
		readarray -t args < <(sudo k8s kubectl get clusterrole | awk '/chaos-mesh/ {print $1}')
		timeout 30 sudo k8s kubectl delete clusterrole "${args[@]}" || true
	fi

	if sudo k8s kubectl get crd | grep -q 'chaos-mesh.org'; then
		echo "deleting crds"
		readarray -t args < <(sudo k8s kubectl get crd | awk '/chaos-mesh.org/ {print $1}')
		timeout 30 sudo k8s kubectl delete crd "${args[@]}" || true
	fi

	if [ -n "${chaos_mesh_ns}" ] && sudo k8s helm repo list --namespace="${chaos_mesh_ns}" | grep -q 'chaos-mesh'; then
		echo "uninstalling chaos-mesh helm repo"
		sudo k8s helm uninstall chaos-mesh --namespace="${chaos_mesh_ns}" || true
	fi
}

echo "Destroying chaos mesh in ${chaos_mesh_ns}"
destroy_chaos_mesh
