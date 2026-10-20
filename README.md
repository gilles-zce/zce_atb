# zce_atb — banc de test automatique WATTEL'GO / WATTEL'WALL

Automatisation du plan de non-régression fonctionnelle
(`ZCE_PLUG_MQTT/docs/functional_smoke_test_plan.md`) pour les trois produits
du dépôt fusionné (prise ZCE MQTT, Wattel EM, OCPP), conformément à la
spécification **WATTELGO-ATB-SPEC-1.1**.

Le banc est une **intégration de briques internes existantes** :

| Brique | Rôle ici |
|---|---|
| `production_bench` (dépendance Poetry par chemin) | liaison série (console de logs), **CLI usine sur BLE** (`zce-cli`, WATTELGO-TB-CLI-003), flash esptool |
| `em_mqtt` (`test_env/em_mqtt`, dépendance par chemin) | broker amqtt, observateur de topics (décodage trames + CRC8), serveur websocket de commandes |
| gateway mTLS `app_server.py` (:9444) | scénarios OTA (phase suivante) |
| `scripts/smoke_hcb.ps1` (dépôt firmware) | étage hors cible (§1) — le banc commence où il s'arrête |

## Prérequis

- Python **3.13** (contrainte commune production-bench / em-mqtt) + Poetry ;
- dépôts frères présents : `C:\workspace\production_bench` et
  `C:\workspace\test_env\em_mqtt` (chemins relatifs du `pyproject.toml`) ;
- une prise **provisionnée** (PKI de test, `prodid` + `provision/root_ca`),
  cf. plan de smoke test, préambule ;
- un adaptateur **Bluetooth** sur le poste : la CLI usine passe par BLE
  (service `zce-cli`), la console série (ex. COM19) ne porte que les logs ;
- port 1883 libre : **arrêter l'application em-mqtt (GUI)** pendant une
  passe pytest — le harnais fournit son propre broker/observateur/serveur
  de commandes — et penser au mosquitto WSL relayé par wslrelay. Le
  pre-flight refuse de démarrer sinon.

## Installation et première passe

```powershell
cd C:\workspace\zce_atb
poetry install
Copy-Item config\station.example.yaml config\station.yaml   # puis adapter
poetry run pytest -m tronc_commun                 # à chaque passe
poetry run pytest -m "tronc_commun or backend"    # + extension §8 (seconde instance em_mqtt, ports +10)
```

La configuration du poste peut aussi être pointée par `ZCE_ATB_CONFIG`.
Chaque session archive le log UART complet dans `log/`.

## Structure

```
src/zce_atb/
  config.py      # YAML de poste -> dataclasses validées
  dut.py         # Dut : console de logs (mark/wait_for_log) + CLI usine BLE (reconnexion auto)
  harness.py     # BenchHarness : services em_mqtt en boucle asyncio dédiée
  preflight.py   # contrôles d'avant-session (port 1883, *IDN? phase 2)
  drivers/       # ASR-2100, NPA701 (squelettes SCPI, phase 2)
tests/
  conftest.py            # fixtures station / dut / harness / fresh_boot
  oracles.py     # flags relais de la télémétrie, codes du topic errors, journal
  tronc_commun/
    test_boot.py         # §2 boot et intégrité de base
    test_connectivity.py # §3 identité, broker, serveur de commandes, CNX-10
    test_errors.py       # §5 chaîne d'erreurs : MEDIUM, HIGH latché, déblocage par reset
  extensions/
    test_backend.py      # §8 set_backend : migration A→B et retour, rejet tout-ou-rien
```

## Principes

- **Jamais de sleep fixe** : `dut.mark()` avant l'action, puis
  `dut.wait_for_log(pattern, since=mark)` / `harness.wait_for(kind, predicate)`
  avec des timeouts généreux (configuration de poste).
- **Échec explicite plutôt que faux négatif** : les pre-flights arrêtent la
  session si le banc est ambigu.
- **Marqueurs pytest** par section du plan : `tronc_commun`, `ota`,
  `backend`, `metrology`, `instruments`.

## État (phase 1) et suite

Fait et **validé sur cible le 2026-10-20** (Wattel EM, 8 tests verts en
~2 min) : fixtures, pre-flight 1883, tests §2 (boot, cause de reset,
version) et §3 (identité, broker via télémétrie, availability, serveur de
commandes, reprise CNX-10 après redémarrage du serveur).

Enseignements de la mise au point, à connaître avant d'étendre :

- les dicts décodés par em_mqtt n'ont pas de champ `kind` — le harnais les
  étiquette lui-même (`_TaggingDecoder`) ;
- la session BLE tombe à chaque reboot : `reset()` est best-effort et la
  session est rouverte au prochain `cli()` ;
- le client websocket du firmware ne répond ni aux pings ni aux trames
  CLOSE : le serveur de commandes est construit avec `ping_interval=None`
  et `close_timeout=2`, et CNX-10 observe la reconnexion effective plutôt
  que l'événement transitoire (CLOSED ou DISCONNECTED selon le timing).

§5 chaîne d'erreurs **validé sur cible le 2026-10-20** (`test_errors.py` :
MEDIUM non latché, HIGH latché avec coupure et refus de `relay on`,
déblocage par `{"cmd":"reset"}` avec BOOT 03 et `errors` purgé ; §5.4 Wi-Fi
sauté jusqu'à l'AP pilotable) — 11 verts + 1 sauté en ~3 min 30.
Mécanique ajoutée : `dut.cli_admin()` (armement
`help admin_mode`, retombe à chaque boot), `dut.journal_list()` (parseur des
lignes de `journal list`), `dut.forget_cli()` après un reboot commandé par
websocket, et le module `oracles.py`.

§8 `set_backend` **validé sur cible le 2026-10-20** (`extensions/test_backend.py`,
marqueur `backend` : migration A→B par une seconde instance em_mqtt décalée
de +10 ports, retour par `backend reset`, rejet tout-ou-rien ; 2 verts en
~1 min 20). À savoir : `backend show` ne rapporte que l'état **effectif**
résolu au boot, jamais un override persisté non appliqué — la fixture
`backend_defaults` fait donc toujours `backend reset` (idempotent) avant et
après chaque test, pour ne jamais laisser la prise pointer sur B.

À venir, dans l'ordre de la roadmap SPEC-1.1 §5 :

1. fin de phase 1 : arrêt **brutal** du serveur de commandes (second
   chemin CNX-10) ; ré-énumération USB Serial/JTAG (produit EM) ;
2. phase 2 : drivers ASR-2100 / NPA701 validés sur instruments (manuels
   dans le dépôt de la spec, `doc/`), charge commutée, §4 télémétrie avec
   oracle métrologique, §6 coupure secteur ;
3. phase 3 : fixture CSMS OCPP (paquet `ocpp`), paramétrage produit/backend ;
4. phase 4 : poste banc dédié (SBC), nightly, archivage des rapports,
   génération de la fiche d'exécution xlsx depuis les résultats.
