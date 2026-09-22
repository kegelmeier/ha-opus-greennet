# Changelog: Vier neue EnOcean-Geräte auf Basis v0.3.3b0

**Basis:** `kegelmeier/ha-opus-greennet` Tag `v0.3.3b0` (Commit `7fee4673`, main-HEAD zum Zeitpunkt der Portierung).
**Ziel:** Vier zusätzliche EnOcean-Geräte (ursprünglich prototypisch im Fork `fubu2k/ha-opus-greennet` umgesetzt) sauber und ohne Verlust der v0.3.3b0-Härtung in die offizielle Codebasis integrieren.

Dieses Dokument richtet sich an die Maintainer von `kegelmeier/ha-opus-greennet` und beschreibt, **was** hinzugefügt wurde, **warum** bestimmte Entscheidungen so getroffen wurden, und **welche Bugs** während der Portierung gefunden und behoben wurden – inklusive zweier Bugs, die tatsächlich in der offiziellen v0.3.3b0-Basis selbst lagen (nicht im Fork).

---

## 1. Neue Geräte

| Gerät | EEP(s) | Hersteller | Plattform(en) |
|---|---|---|---|
| HOPPE Fenstergriff ohne eLock | `F6-10-00`, `D2-03-10` | HOPPE | `sensor` (Griffstellung) |
| HOPPE Fenstergriff mit eLock | `D2-06-40` | HOPPE | `lock` (Sperre) + `sensor` (Griffstellung, Entsperranfrage) |
| Rauchwarnmelder (RWM) | `F6-05-02` | Jaeger Direkt | `binary_sensor` (Rauchalarm, Batterie schwach) |
| SMS Anwesenheitssensor | `A5-07-03` | Jaeger Direkt / OPUS | `binary_sensor` (Bewegung) + `sensor` (Helligkeit, Versorgungsspannung, Batteriestand) |

Alle vier EEPs, Function-Keys und Konstanten sind in `const.EEP_MAPPINGS` bzw. den neuen `KEY_*`-Konstanten dokumentiert.

---

## 2. Architektur-Entscheidung: Zentrale, deklarative Entity-Registry

**Problem im Ursprungs-Fork:** Neue Geräte wurden dort über verstreute, hartkodierte `if device.primary_eep == "..."`-Abfragen direkt in `binary_sensor.py`/`sensor.py` eingebaut. Das verletzt das Open/Closed-Prinzip und führt bei jedem neuen Gerät zu Änderungen an mehreren Plattform-Dateien gleichzeitig – ein klassischer Grund für Merge-Konflikte und Regressionen bei HA-Updates.

**Lösung:** Neues Modul **`entity_descriptions.py`**. Jede unterstützte Zusatz-Entity (Sensor, Binary Sensor, Lock) wird dort einmal deklarativ beschrieben:

```python
OpusBinarySensorDescription(
    key="motion",
    translation_key="motion",
    applies_to=_eeps("A5-07-03"),
    value_fn=_channel_attr("motion_detected"),
    device_class=BinarySensorDeviceClass.MOTION,
)
```

`applies_to` ist ein Prädikat (`Callable[[EnOceanDevice], bool]`) – entweder ein exakter EEP-Match oder eine allgemeine Bedingung wie `device.is_climate`. Die Plattform-Dateien (`binary_sensor.py`, `sensor.py`, `lock.py`) enthalten dadurch **keine gerätespezifische Logik mehr**; sie iterieren generisch über `entity_descriptions.descriptions_for(device, platform)`. Auch die bereits bestehenden Climate-Diagnose-Entities (Fenster-offen, Aktor-Fehler, Batterie-Sensoren) wurden aus Konsistenzgründen in dasselbe Schema migriert.

**Folge für zukünftige Geräte:** Ein neues Gerät benötigt in der Regel nur einen neuen Eintrag in `const.py` (EEP-Mapping, Function-Keys) und `entity_descriptions.py` (welche Entities es bekommt) sowie ggf. neue Felder/Parsing-Zweige in `enocean_device.py`. Die Plattform-Dateien bleiben unangetastet.

**Wichtiger Bugfix zu diesem Pattern:** Das Beschreibungsobjekt darf niemals unter dem Attributnamen `self.entity_description` gespeichert werden – dieser Name ist von `homeassistant.helpers.entity.Entity` reserviert (wird intern für `translation_placeholders`/`name` gelesen) und führte zunächst zu `AttributeError: '...' object has no attribute 'translation_placeholders'` bei jeder Entity. Fix: Attribut heißt `self._opus_description`.

---

## 3. Protokoll-Anpassung im Coordinator: mehrere Key/Value-Container

Das reale OPUS-Gateway liefert Zustände nicht immer unter `states/{n}/key` + `states/{n}/value` (wie ursprünglich angenommen), sondern für den Rauchwarnmelder unter `transmitModes/{n}/key` + `.../value`. Statt dies hartkodiert für ein weiteres Gerät im Coordinator zu behandeln, wurde eine neue Konstante eingeführt:

```python
# const.py
INDEXED_STATE_CONTAINERS: Final = ("states", "transmitModes")
```

`coordinator._device_state_functions()` und `coordinator._apply_known_device_state_property()` iterieren jetzt generisch über alle konfigurierten Container. **Ein drittes Gerät mit einem dritten Container-Namen erfordert nur eine Zeile in `const.py`, keine Coordinator-Änderung.**

---

## 4. Zwei Bugs, die in der offiziellen v0.3.3b0-Basis selbst gefunden wurden

Diese beiden Punkte sind unabhängig von den vier neuen Geräten und potenziell auch für andere v0.3.3b0-Nutzer relevant:

### 4.1 Deadlock beim Gateway-Handshake (Doppel-Subscription)

`async_setup()` hielt eine **permanente** Subscription auf `TOPIC_GET_ANSWER_SYSTEM_INFO` (→ `_handle_system_info`) **zusätzlich** zu der Subscription, die `MQTTRequestManager.async_request()` intern für denselben Topic-String während `_async_refresh_gateway()` öffnet. Home Assistants MQTT-Client feuert für ein bereits abonniertes Topic kein zweites `SUBACK`, wodurch die zweite (request-gebundene) Subscription nie fertig wurde und der verpflichtende Gateway-Probe nach `REQUEST_TIMEOUT` (10 s) **zuverlässig bei jedem Setup** fehlschlug – unabhängig von `eag_id` oder Gateway-Zustand.

**Fix:** Permanente Subscriptions auf `TOPIC_GET_ANSWER_SYSTEM_INFO`/`TOPIC_GET_ANSWER_SYSTEM_UPTIME` entfernt. Beide Werte kommen nie unaufgefordert vom Gateway, sondern ausschließlich als Antwort auf ein selbst gesendetes GET – eine Dauer-Subscription war unnötig und hat den Handshake blockiert. Uptime läuft jetzt über eine eigene, kurzlebige Anfrage (`_async_request_system_uptime()`).

### 4.2 System-Info-Probe blockiert Verfügbarkeit unnötig

Nach Fix 4.1 zeigte sich in der Praxis: Manche Mosquitto-Bridge-Konfigurationen (oder Gateway-Firmwares) beantworten `get/config/system/info` gar nicht – vermutlich weil ältere/engere Bridge-Regeln nur konkrete Sub-Topics statt `get/#`/`getAnswer/#`-Wildcards relayen. Da dieser Endpoint rein diagnostischer Natur ist (Modell-/Firmware-String für die Diagnose-Ausgabe), dieser aber bislang `async_setup()` **hart blockierte**, führte ein fehlendes Bridge-Mapping zu dauerhaftem `gateway_unavailable`, obwohl Geräte-Discovery und -Steuerung technisch einwandfrei funktioniert hätten.

**Fix:** Der System-Info-Aufruf in `_async_refresh_gateway()` ist jetzt best-effort: Ein Timeout/Fehler wird geloggt (`WARNING`), verhindert aber nicht mehr, dass das Gateway als verfügbar markiert wird. Geräte-Discovery (`get/devices` → `getAnswer/devices/#`) bleibt unverändert zwingend erforderlich, da sie für die eigentliche Funktion der Integration unerlässlich ist.

---

## 5. Geänderte / neue Dateien im Überblick

| Datei | Art | Kurzbeschreibung |
|---|---|---|
| `const.py` | geändert | EEP-Mappings + Function-Keys für 4 Geräte, `INDEXED_STATE_CONTAINERS`, `TOPIC_WINDOW_HANDLE_ACCESS` |
| `entity_descriptions.py` | **neu** | Zentrale deklarative Entity-Registry (Open/Closed-Prinzip) |
| `enocean_device.py` | geändert | Neue `EnOceanChannel`/`EnOceanDevice`-Felder + Parsing-Zweige für alle 4 Geräte, unter Beibehaltung der v0.3.3b0-Parsing-Strenge (`_parse_number`, `_update_numeric_field`, `state_revision`) |
| `coordinator.py` | geändert | Generalisierter Container-Parser (`states`/`transmitModes`), Battery-/dBm-Fastpath, `async_set_window_handle_lock()`, Fix 4.1 + 4.2 |
| `mqtt_transport.py` | geändert (nur Logging) | Zusätzliches Debug-/Warn-Logging um Subscribe/SUBACK/Publish/Response, funktional identisch zur Basis |
| `entity.py` | geändert | Snapshot-Guard (`_channel_state_snapshot`/`_channel_state_matches`) unverändert übernommen, neue generische Basisklassen für beschreibungsgetriebene Entities, Fix des `entity_description`-Namenskonflikts |
| `binary_sensor.py` | geändert | Keine EEP-Sonderfälle mehr, rein generisch über `entity_descriptions` |
| `sensor.py` | geändert | Keine EEP-Sonderfälle mehr, rein generisch über `entity_descriptions` |
| `lock.py` | **neu** | HOPPE-eLock-Entity (bekannt limitiert, siehe unten), Snapshot-Guard, generisch registriert |
| `__init__.py` | geändert | `Platform.LOCK` in `PLATFORMS` ergänzt (einzige Änderung) |
| `strings.json` / `translations/en.json` | geändert | Neue `translation_key`s: `motion`, `smoke_alarm`, `battery_low`, `illuminance`, `supply_voltage`, `battery_level`, `handle_state` (+Enum), `unlock_request` (+Enum), `window_handle_lock` |
| `translations/de.json` | **neu** | Vollständige deutsche Übersetzung (existierte in v0.3.3b0 noch nicht) |

Unverändert übernommen: `climate.py`, `cover.py`, `event.py`, `light.py`, `switch.py`, `config_flow.py`, `diagnostics.py`, `services.yaml`.

---

## 6. Bekannte Einschränkung: HOPPE eLock-Steuerung

`async_set_window_handle_lock()` publiziert auf einen Topic (`.../functions/0/value`, Payload `allowed`/`notAllowed`) ohne dokumentierte `putAnswer`-Bestätigung durch das Gateway. In der Praxis ändert dieser Befehl den physischen Sperrzustand nicht zuverlässig. Die passiven Sensoren (Griffstellung, Entsperranfrage) funktionieren unabhängig davon zuverlässig.
