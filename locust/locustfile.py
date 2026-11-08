
from locust import HttpUser, task, between


class StockflowUser(HttpUser):
    # Pausa entre solicitudes para simular usuarios reales
    wait_time = between(1, 3)

    @task(5)
    def consultar_cuenta(self):
        self.client.get(
            "/api/cuenta",
            name="/api/cuenta"
        )

    @task(2)
    def consultar_metricas(self):
        self.client.get(
            "/api/metrics",
            name="/api/metrics"
        )
