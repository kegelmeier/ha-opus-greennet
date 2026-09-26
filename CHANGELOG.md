# Changelog

## [Unreleased] – fix/hoppe-writeback-uptime-probe

### Fixed

#### `lock.py` – HOPPE AutoLock: GUI-Writeback vollständig blockiert

**Problem:** `async_lock()` und `async_unlock()` riefen den Coordinator auf
und setzten anschließend `channel.lock_state` lokal – ohne dass jemals ein
MQTT-Telegramm das Gateway erreicht hätte (die Mosquitto-Bridge leitet
`stream/#` ausschließlich **inbound** weiter). Das Ergebnis war ein
irreführend „verriegelter“ HA-Zustand, der nicht mit dem physischen
Griffzustand übereinstimmte.

**Fix:** Beide Methoden werfen jetzt sofort eine `HomeAssistantError` mit
einer Fehlermeldung. Kein Coordinator-Aufruf, keine lokale
Zustandsmanipulation, kein `async_write_ha_state()`. Die GUI zeigt beim
Antippen eine sichtbare Meldung; das Gateway-Push via
`stream/device/#/states/…` bleibt die einzige Quelle für `lock_state`.

**Betroffene Dateien:** `custom_components/opus_greennet/lock.py`

---

#### `mqtt_transport.py` – Gateway-Probe auf `/uptime` umgestellt

**Problem:** `async_probe_gateway()` fragte `get/config/system/info` ab.
Das OPUS-IQ-DOT Gateway (Firmware v1.21.30) implementiert diesen Endpunkt
**nicht**. Folge: bei jedem Integrationsstart erschien nach 10 Sekunden
folgende Warnung im HA-Log:

```
WARNING (MainThread) [custom_components.opus_greennet.mqtt_transport]
OPUS request timed out for device 05215569
(topic=EnOcean/05215569/get/config/system/info,
 answer=EnOcean/05215569/getAnswer/config/system/info,
 subscribed=True).
```

Danach deaktivierte der Coordinator alle weiteren System-Info-Abfragen:

```
WARNING (MainThread) [custom_components.opus_greennet.coordinator]
OPUS gateway 05215569 did not answer get/config/system/info
(request_timeout). Disabling further automatic system-info probes …
```

**Fix:** `async_probe_gateway()` verwendet jetzt:
- **Publish-Topic:** `EnOcean/{eag_id}/get/config/system/uptime`
- **Subscribe-Topic:** `EnOcean/{eag_id}/getAnswer/config/system/uptime`
- `require_status=True` → validiert `header.httpStatus == 200`

Verifizierte Gateway-Antwort (live via MQTT Explorer, Firmware v1.21.30):
```json
{
  "header": {
    "httpStatus": 200,
    "content": "Uptime",
    "gateway": "OPUS-IQ-DOT v1.21.30",
    "timestamp": "2026-09-26T08:46:41.293+0200"
  },
  "systemUptimeResponse": {
    "uptime": 16766
  }
}
```

Das 10-Sekunden-Timeout beim Integrationsstart entfällt vollständig.

**Betroffene Dateien:** `custom_components/opus_greennet/mqtt_transport.py`

**Import-Änderung:**
```python
# Vorher
from .const import (
    DOMAIN, TOPIC_BASE,
    TOPIC_GET_ANSWER_SYSTEM_INFO,
    TOPIC_GET_SYSTEM_INFO,
)

# Nachher
from .const import (
    DOMAIN, TOPIC_BASE,
    TOPIC_GET_ANSWER_SYSTEM_UPTIME,
    TOPIC_GET_SYSTEM_UPTIME,
)
```

---

### Empfohlene Folge-Änderungen (nicht in diesem Branch enthalten)

#### `coordinator.py` – Uptime-Wert korrekt aus verschachteltem JSON extrahieren

Die aktuelle Implementierung speichert die rohe MQTT-Nutzlast als String:
```python
self.gateway_uptime = payload  # ← falsch
```

Korrekte Extraktion:
```python
uptime_response = data.get("systemUptimeResponse")
if not isinstance(uptime_response, dict):
    raise request_error("request_rejected", self.eag_id, "Missing systemUptimeResponse")
uptime = uptime_response.get("uptime")
if isinstance(uptime, bool) or not isinstance(uptime, int) or uptime < 0:
    raise request_error("request_rejected", self.eag_id, "Invalid system uptime")
self.gateway_uptime = uptime  # integer, Sekunden seit Boot
self.gateway_info = data.get("header", {})
```

---

### Tests

- `test_probe_verifies_gateway_and_cleans_subscription()`: Topic-Erwartung
  von `.../get/config/system/info` auf `.../get/config/system/uptime` ändern
- Neuer Regressionstest für `async_lock()` / `async_unlock()`: sicherstellen,
  dass kein MQTT-Publish ausgeführt wird und der Kanalzustand unverändert bleibt
