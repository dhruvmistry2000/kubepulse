variable "cluster_name" {
  description = "kind cluster name. The node container is <name>-control-plane."
  type        = string
  default     = "kubepulse"
}

variable "node_image" {
  description = "kind node image. Pin this so a later apply does not jump Kubernetes versions."
  type        = string
  default     = "kindest/node:v1.33.1"
}

variable "pod_subnet" {
  description = "Pod CIDR. Must match the CIDR given to Cilium."
  type        = string
  default     = "10.244.0.0/16"
}

variable "service_subnet" {
  description = "Service CIDR."
  type        = string
  default     = "10.96.0.0/12"
}

variable "cilium_version" {
  description = "Cilium chart version. Cilium replaces kube-proxy because the host kernel in use here cannot load the netfilter modules kube-proxy needs."
  type        = string
  default     = "1.20.4"
}
