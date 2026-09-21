# HOPPE-Fenstergriff D2-06-40

Diese Variante ergänzt `ha-opus-greennet` um dynamische Unterstützung für beliebig viele HOPPE-Fenstergriffe.

## Enthalten

- Home-Assistant-Lock-Entität zum Sperren (`notAllowed`) und Freigeben (`allowed`)
- Enum-Sensor `Griffstellung` (`closed` / `open`)
- Enum-Sensor `Entsperranfrage` (`notRequested` / `requested`)
- Parsing indexierter `states/{index}/key`- und `states/{index}/value`-Fragmente
- Registrierung von `Platform.LOCK` und Erweiterung von `EnOceanChannel`
- Englische und deutsche Entitätsübersetzungen

## Installation

Den Ordner `custom_components/opus_greennet` über die vorhandene Integration kopieren und Home Assistant neu starten. Danach die Integration bei Bedarf neu laden.
