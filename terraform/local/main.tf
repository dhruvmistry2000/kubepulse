resource "kind_cluster" "this" {
  name            = var.cluster_name
  node_image      = var.node_image
  kubeconfig_path = abspath("${path.module}/kubeconfig")
  # Nodes stay NotReady until Cilium is installed, so do not wait for Ready here.
  wait_for_ready = false

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    networking {
      disable_default_cni = true
      kube_proxy_mode     = "none"
      pod_subnet          = var.pod_subnet
      service_subnet      = var.service_subnet
    }

    node {
      role = "control-plane"
    }
  }
}

resource "terraform_data" "cilium" {
  depends_on = [kind_cluster.this]

  triggers_replace = {
    cluster_id = kind_cluster.this.id
    version    = var.cilium_version
    pod_subnet = var.pod_subnet
  }

  provisioner "local-exec" {
    command     = "${path.module}/cilium.sh"
    interpreter = ["bash"]

    environment = {
      KUBECONFIG     = abspath("${path.module}/kubeconfig")
      CLUSTER_NAME   = var.cluster_name
      CILIUM_VERSION = var.cilium_version
      POD_SUBNET     = var.pod_subnet
      NETWORK_ENV    = abspath("${path.module}/cluster.env")
    }
  }
}

data "external" "network" {
  depends_on = [terraform_data.cilium]
  program    = ["bash", "${path.module}/network.sh", abspath("${path.module}/cluster.env")]
}
