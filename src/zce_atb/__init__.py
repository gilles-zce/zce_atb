"""zce_atb — banc de test automatique WATTEL'GO / WATTEL'WALL.

Automatise le plan de non-régression fonctionnelle
(ZCE_PLUG_MQTT/docs/functional_smoke_test_plan.md) sur les trois produits du
dépôt fusionné, conformément à WATTELGO-ATB-SPEC-1.1. Le banc est une
intégration de briques internes existantes : production_bench (CLI UART,
flash) et em_mqtt (broker, observateur, serveur de commandes).
"""

__version__ = "0.1.0"
