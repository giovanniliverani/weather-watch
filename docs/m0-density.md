# M0 density check

Generated 2026-09-16T13:36:05Z by `eww report density --days 30`. Window: events last observed at or after 2026-08-17T13:36:05Z (canonical events with a centroid, the same definition `eww export` uses).

**Verdict: PASS** (4 of 4 criteria met).

## Totals

| Measure | Events |
|---|---:|
| Events observed in the window | 3228 |
| GDACS wildfires below Orange (excluded from every criterion) | 1585 |
| **Counted** (after the exclusion) | **1643** |
| EONET events that mirror a GDACS event also present (M0 has no cross-source merging) | 940 |
| Counted after also removing those mirrors | 703 |

Events by source combination (all events in the window): eonet 1051, gdacs 2177.

## Criteria

| Criterion | Value | Target | Result |
|---|---:|---:|---|
| at least 40 events after excluding GDACS wildfires below Orange | 1643 | 40 | PASS |
| at least 4 hazard types with 3 or more events each | 5 | 4 | PASS |
| at least 4 continents with 3 or more events each | 6 | 4 | PASS |
| at least 3 non-wildfire events in Europe | 50 | 3 | PASS |

## Events by hazard type

| Hazard type | Counted | All in window | Counted, mirrors removed |
|---|---:|---:|---:|
| drought | 13 | 13 | 13 |
| earthquake | 484 | 484 | 484 |
| flood | 111 | 111 | 71 |
| tropical_cyclone | 33 | 33 | 33 |
| volcano | 2 | 2 | 2 |
| wildfire | 1000 | 2585 | 100 |

## Events by continent

Continent from `event.country_iso3` via `eww/data/countries.csv` (GeoNames countryInfo.txt, CC BY 4.0). A country belongs to one continent, so an earthquake in Russia's Far East counts as Europe. `Unknown` means the feed gave no country and none could be read from the title: mostly storms over open ocean and EONET events whose title carries no country.

| Continent | Counted | All in window | Counted, mirrors removed |
|---|---:|---:|---:|
| Africa | 615 | 1673 | 15 |
| Asia | 318 | 406 | 246 |
| Europe | 109 | 247 | 41 |
| North America | 165 | 190 | 147 |
| Oceania | 167 | 295 | 70 |
| South America | 148 | 295 | 64 |
| Unknown | 121 | 122 | 120 |

## Non-wildfire events in Europe

50 events.

| Hazard type | Title | Country | Sources |
|---|---|---|---|
| drought | Drought in Austria, Bosnia  and  Herzegovina, Belgium, Belarus, Switzerland, Czech Republic, Germany, Denmark, Spain, France, Croatia, Hungary, Ireland, Italy, Liechtenstein, Luxembourg, Netherlands, Poland, Romania, Serbia, Sweden, Slovenia, Slovakia, San Marino, Ukraine, , | AUT | gdacs |
| earthquake | Earthquake in Albania | ALB | gdacs |
| earthquake | Earthquake in Greece | GRC | gdacs |
| earthquake | Earthquake in Greece | GRC | gdacs |
| earthquake | Earthquake in Greece | GRC | gdacs |
| earthquake | Earthquake in Greece | GRC | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russia | RUS | gdacs |
| earthquake | Earthquake in Russian Federation | RUS | gdacs |
| earthquake | Earthquake in Russian Federation | RUS | gdacs |
| earthquake | Earthquake in Russian Federation | RUS | gdacs |
| earthquake | Earthquake in Spain | ESP | gdacs |
| earthquake | Earthquake in Spain | ESP | gdacs |
| earthquake | Earthquake in Spain | ESP | gdacs |
| flood | Flood in Austria | AUT | gdacs |
| flood | Flood in Austria | AUT | gdacs |
| flood | Flood in Austria 1104115 | AUT | eonet |
| flood | Flood in Austria 1104137 | AUT | eonet |
| flood | Flood in Bulgaria | BGR | gdacs |
| flood | Flood in Bulgaria 1104120 | BGR | eonet |
| flood | Flood in Croatia | HRV | gdacs |
| flood | Flood in Croatia 1104153 | HRV | eonet |
| flood | Flood in Czech Republic | CZE | gdacs |
| flood | Flood in Czech Republic 1104118 | CZE | eonet |
| flood | Flood in France | FRA | gdacs |
| flood | Flood in France | FRA | gdacs |
| flood | Flood in France 1104149 | FRA | eonet |
| flood | Flood in Germany | DEU | gdacs |
| flood | Flood in Germany | DEU | gdacs |
| flood | Flood in Germany 1104150 | DEU | eonet |
| flood | Flood in Ireland | IRL | gdacs |
| flood | Flood in Ireland 1104127 | IRL | eonet |
| flood | Flood in Italy | ITA | gdacs |
| flood | Flood in Lithuania | LTU | gdacs |
| flood | Flood in Lithuania 1104138 | LTU | eonet |
| flood | Flood in Poland | POL | gdacs |
| flood | Flood in Poland | POL | gdacs |
| flood | Flood in Poland 1104144 | POL | eonet |
| flood | Flood in Slovenia | SVN | gdacs |
| flood | Flood in Slovenia 1104152 | SVN | eonet |
| flood | Flood in Spain | ESP | gdacs |
| flood | Flood in Spain | ESP | gdacs |
| flood | Flood in Spain 1104151 | ESP | eonet |

## Decision rule (docs/architecture.md §4, M0)

The world and Europe both pass: the spine is thick enough, M1 proceeds as planned.
