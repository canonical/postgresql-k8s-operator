#!/bin/bash

set -Eeuo pipefail

chaos_mesh_ns=$1

if [ -z "${chaos_mesh_ns}" ]; then
	echo "Error: missing mandatory argument. Aborting" >&2
	exit 1
fi

destroy_chaos_mesh() {
	echo "deleting api-resources"
	for i in $(kubectl api-resources | awk '/chaos-mesh/ {print $1}'); do
	    timeout 30 kubectl delete "${i}" --all --all-namespaces || true
	done

	if kubectl -n "${chaos_mesh_ns}" get mutatingwebhookconfiguration | grep -q 'choas-mesh-mutation'; then
		timeout 30 kubectl -n "${chaos_mesh_ns}" delete mutatingwebhookconfiguration chaos-mesh-mutation || true
	fi

	if kubectl -n "${chaos_mesh_ns}" get validatingwebhookconfiguration | grep -q 'chaos-mesh-validation'; then
		timeout 30 kubectl -n "${chaos_mesh_ns}" delete validatingwebhookconfiguration chaos-mesh-validation || true
	fi

	if kubectl -n "${chaos_mesh_ns}" get validatingwebhookconfiguration | grep -q 'chaos-mesh-validate-auth'; then
		timeout 30 kubectl -n "${chaos_mesh_ns}" delete validatingwebhookconfiguration chaos-mesh-validate-auth || true
	fi

	if kubectl get clusterrolebinding | grep -q 'chaos-mesh'; then
		echo "deleting clusterrolebindings"
		readarray -t args < <(kubectl get clusterrolebinding | awk '/chaos-mesh/ {print $1}')
		timeout 30 kubectl delete clusterrolebinding "${args[@]}" || true
	fi

	if kubectl get clusterrole | grep -q 'chaos-mesh'; then
		echo "deleting clusterroles"
		readarray -t args < <(kubectl get clusterrole | awk '/chaos-mesh/ {print $1}')
		timeout 30 kubectl delete clusterrole "${args[@]}" || true
	fi

	if kubectl get crd | grep -q 'chaos-mesh.org'; then
		echo "deleting crds"
		readarray -t args < <(kubectl get crd | awk '/chaos-mesh.org/ {print $1}')
		timeout 30 kubectl delete crd "${args[@]}" || true
	fi

	if [ -n "${chaos_mesh_ns}" ] && sg snap_microk8s -c "microk8s.helm3 repo list --namespace=${chaos_mesh_ns}" | grep -q 'chaos-mesh'; then
		echo "uninstalling chaos-mesh helm repo"
		sg snap_microk8s -c "microk8s.helm3 uninstall chaos-mesh --namespace=\"${chaos_mesh_ns}\"" || true
	fi
}

echo "Destroying chaos mesh in ${chaos_mesh_ns}"
destroy_chaos_mesh
