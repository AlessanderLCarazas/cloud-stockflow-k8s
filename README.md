# Cloud StockFlow -- Commit profesional

**Tipo:** feat

**Mensaje de commit:**

``` text
feat: implement cloud-native StockFlow platform with Kubernetes autoscaling and AWS infrastructure
```

## Descripción

Implement a cloud-native banking inventory management platform using
AWS, Docker, Kubernetes (K3s) and a custom EC2 provisioning mechanism.

### Infrastructure and Cloud Architecture

-   Provision AWS infrastructure using Terraform.
-   Configure EC2 instances to host the K3s Kubernetes cluster.
-   Deploy a distributed architecture with one Control Plane and
    multiple Worker nodes.
-   Configure Amazon ECR repositories for backend and frontend container
    images.
-   Configure AWS networking and security groups.

### Containerization and Deployment

-   Containerize backend and frontend applications using Docker.
-   Implement automated CI/CD pipeline with GitHub Actions.
-   Execute backend unit tests using pytest before deployment.
-   Build and publish Docker images to Amazon ECR.
-   Automate remote deployment through SSH.
-   Configure Kubernetes imagePullSecrets for private ECR image access.
-   Apply Kubernetes manifests and restart application Deployments.

### Kubernetes Orchestration

-   Deploy PostgreSQL, backend and frontend workloads.
-   Configure Kubernetes Deployments and Services.
-   Expose application services through NodePort.
-   Implement Horizontal Pod Autoscaling (HPA) for backend workloads.
-   Configure CPU-based scaling with a 50% utilization target.
-   Define minimum and maximum backend replicas.

### Infrastructure Autoscaling

-   Implement a custom Python-based EC2 provisioning service.
-   Monitor Kubernetes Pods and detect scheduling failures caused by
    insufficient capacity.
-   Integrate the provisioner with AWS to launch additional EC2 Worker
    instances.
-   Configure worker limits, monitoring intervals and cooldown periods.
-   Register additional capacity into the K3s cluster.

### Monitoring and Observability

-   Implement Kubernetes status monitoring through the backend API.
-   Expose information about Nodes, Pods, Deployments, HPA and
    Kubernetes events.
-   Develop a real-time monitoring dashboard.
-   Implement automatic dashboard refresh.
-   Display infrastructure resource utilization and cluster status.

### Stress Testing and Performance Evaluation

-   Integrate configurable load testing using Locust.
-   Implement application stress testing endpoints.
-   Evaluate backend scaling behavior under simulated workloads.
-   Monitor CPU, memory, replicas and Kubernetes scheduling events.

### Database

-   Integrate PostgreSQL as the persistent database.
-   Deploy PostgreSQL within Kubernetes.
-   Preserve database state and support database backup and restoration.

This implementation establishes an automated cloud-native platform with
container orchestration, application-level autoscaling, infrastructure
provisioning, continuous deployment and operational monitoring.
