# Phase 23 Strategy Sanity Table

Generated from local `data/edge.db` after the Phase 23 FFC snapshot and post-review strategy-threshold calibration. Auction teams are excluded from the snake-draft classifier; keeper picks are excluded from triggers.

## Coverage

- Non-pre-draft `is_me` teams: 114
- Qualifying snake teams classified: 112
- Auction teams excluded: 2
- Keeper picks excluded from triggers: 0
- Eligible snake picks: 1792
- Eligible picks missing ESPN draft-time ADP: 0
- Eligible picks missing FFC current market ADP: 8
- Distinct drafted-player FFC id coverage: 157 / 161 (97.5%)
- Distinct drafted-player FFC ADP coverage: 157 / 161 (97.5%)
- FFC snapshot exclusions: 4 distinct drafted players (3 D/ST, 1 K); resolver failures: 0
- `adp_snapshots` source `ffc`: id 1: pulled_at `2026-08-05T01:18:15.724128`, format `ppr`, teams `10`

## Calibrated Thresholds

Round-equivalent = `overall_pick / league_size`.

| Label | Final rule | Calibration note |
|---|---|---|
| Zero RB | No RB through `5.0`. | Strict end-of-round-5 reading retained; current data has no first RB after `5.0`. |
| Hero RB | Exactly one RB through `5.0`; second RB absent or after `5.0`. | Closes the old 5.0-7.0 fallthrough. |
| Robust RB | At least two RBs through `2.0`, or at least three RBs through `5.0`. | `2nd RB` median is `2.05`; `3rd RB` median is `5.55`, so Robust now means genuinely early RB concentration. |

## Trigger Input Distribution

| Column | Min | P10 | P25 | Median | P75 | P90 | Max | Missing |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1st RB | 0.10 | 0.11 | 0.38 | 1.00 | 1.60 | 1.79 | 4.30 | 0 |
| 2nd RB | 1.10 | 1.20 | 1.60 | 2.05 | 3.52 | 5.70 | 7.20 | 0 |
| 3rd RB | 2.10 | 2.52 | 3.10 | 5.55 | 7.05 | 8.50 | 13.30 | 0 |
| 1st WR | 0.20 | 0.40 | 0.50 | 2.10 | 3.30 | 4.00 | 5.10 | 0 |
| 2nd WR | 1.30 | 2.40 | 2.90 | 4.10 | 4.90 | 5.99 | 7.10 | 0 |
| 3rd WR | 2.20 | 3.80 | 4.47 | 5.65 | 7.00 | 7.79 | 9.00 | 0 |
| 1st QB | 2.10 | 3.23 | 5.20 | 6.45 | 10.43 | 11.49 | 15.70 | 0 |
| 1st TE | 1.90 | 3.83 | 4.60 | 8.70 | 9.90 | 11.58 | 15.70 | 0 |

## Gap Closure

- Old thresholds classified 19 teams as `Balanced/BPA` despite exactly one RB through `5.0` and a second RB in `(5.0, 7.0]`.
- After widening Hero RB, those 19 teams classify as `Hero RB` unless Autodraft/Absent wins precedence; in this DB all 19 non-autodraft gap teams are now Hero RB.
- Zero RB check: 0 teams have first RB after `5.0`; max first RB is `4.30`.

## Primary Distribution

| Primary label | Count | Pct |
|---|---:|---:|
| Robust RB | 62 | 55.4% |
| Balanced/BPA | 23 | 20.5% |
| Hero RB | 20 | 17.9% |
| Autodraft/Absent | 7 | 6.2% |

## Secondary Distribution

| Secondary label | Count | Pct |
|---|---:|---:|
| Anchor WR | 31 | 27.7% |
| Late-Round QB | 28 | 25.0% |
| Elite TE | 8 | 7.1% |

## Primary x Secondary

| Primary | Anchor WR | Elite TE | Late-Round QB | None |
|---|---:|---:|---:|---:|
| Autodraft/Absent | 0 | 0 | 0 | 7 |
| Balanced/BPA | 15 | 1 | 2 | 5 |
| Hero RB | 16 | 2 | 0 | 2 |
| Robust RB | 0 | 5 | 26 | 31 |

No structurally impossible pairings appear after calibration. `Autodraft/Absent` has no secondary by rule; `Hero RB + Anchor WR` is valid because it describes one early RB plus WR volume through round 5; `Robust RB + Anchor WR` is absent in this DB and would be worth manual review if it appears later.

## Spot Checks

| League | Team | Primary | Secondary | Triggering picks |
|---|---|---|---|---|
| 1 Atlanta Pro H2H Points PPR League | CriminallyWideOpen | Robust RB (0.82) |  | 10: Ashton Jeanty (RB); 11: Omarion Hampton (RB) |
| 2 Seattle Pro H2H Points PPR League | Belichick's Younghoe | Autodraft/Absent (1.00) |  | 2: Bijan Robinson (RB); 19: Trey McBride (TE); 22: Josh Jacobs (RB) |
| 3 New England Pro H2H Points PPR League | RBs…WE HAVE THE MEATS | Hero RB (0.86) | Elite TE | 16: Saquon Barkley (RB); 56: David Montgomery (RB) |
| 4 Atlanta Pro H2H Points PPR League | DoubleBlind | Robust RB (0.82) |  | 9: Ashton Jeanty (RB); 12: Jonathan Taylor (RB) |
| 5 Dallas Pro H2H Points PPR League | Jaylin's Scary Team | Robust RB (0.82) |  | 8: Saquon Barkley (RB); 13: James Cook III (RB) |

## Full Table

| League ID | League | Team | Primary | Primary Conf | Secondary | Secondary Conf | Primary Triggers | Secondary Triggers |
|---:|---|---|---|---:|---|---:|---|---|
| 1 | Atlanta Pro H2H Points PPR League | CriminallyWideOpen | Robust RB | 0.82 |  |  | 10, 11 |  |
| 2 | Seattle Pro H2H Points PPR League | Belichick's Younghoe | Autodraft/Absent | 1.00 |  |  | 2, 19, 22 |  |
| 3 | New England Pro H2H Points PPR League | RBs…WE HAVE THE MEATS | Hero RB | 0.86 | Elite TE | 0.78 | 16, 56 | 25 |
| 4 | Atlanta Pro H2H Points PPR League | DoubleBlind | Robust RB | 0.82 |  |  | 9, 12 |  |
| 5 | Dallas Pro H2H Points PPR League | Jaylin's Scary Team | Robust RB | 0.82 |  |  | 8, 13 |  |
| 6 | New England Pro H2H Points PPR League | Jaylin's Grand Team | Robust RB | 0.82 |  |  | 5, 16 |  |
| 7 | Baltimore Pro H2H Points PPR League | DivaWR | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 3, 18, 23 | 3, 18, 43 |
| 8 | New Orleans Pro H2H Points PPR League | Jaylin's Scary Team | Robust RB | 0.82 |  |  | 10, 11 |  |
| 9 | New York Pro H2H Points PPR League | Jaylin's Finest Team | Balanced/BPA | 0.55 |  |  | 1, 20, 21 |  |
| 10 | Minnesota Pro H2H Points PPR League | CpuDartThrow | Autodraft/Absent | 1.00 |  |  | 2, 19, 22 |  |
| 11 | New York Pro H2H Points PPR League | OCPolicy | Hero RB | 0.86 | Anchor WR | 0.80 | 17, 57 | 4, 24, 37 |
| 12 | Cleveland Pro H2H Points PPR League | RunFirst | Robust RB | 0.82 |  |  | 9, 12 |  |
| 13 | New England Pro H2H Points PPR League | Jaylin's Finest Team | Robust RB | 0.82 |  |  | 5, 16 |  |
| 14 | Indianapolis Pro H2H Points PPR League | DefenceWins | Robust RB | 0.82 | Elite TE | 0.78 | 3, 18 | 23 |
| 15 | New England Pro H2H Points PPR League | Jaylin's Heated Team | Hero RB | 0.86 | Anchor WR | 0.80 | 17, 57 | 4, 24, 37 |
| 16 | Washington Pro H2H Points PPR League | Jaylin's Scary Team | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 9, 12, 29 | 9, 32, 49 |
| 17 | Dallas Pro H2H Points PPR League | Jaylin's Finest Team | Robust RB | 0.82 | Late-Round QB | 0.76 | 2, 19 | 99 |
| 18 | Denver Pro H2H Points PPR League | Jaylin's Finest Team | Robust RB | 0.82 | Late-Round QB | 0.76 | 6, 15 | 115 |
| 19 | New York Pro H2H Points PPR League | Bet My HOUSE | Hero RB | 0.86 | Anchor WR | 0.80 | 18, 58 | 3, 23, 38 |
| 20 | Philadelphia Pro H2H Points PPR League | Jaylin's Hearty Team | Robust RB | 0.82 |  |  | 10, 11 |  |
| 21 | Kansas City Pro H2H Points PPR League | Jaylin's Scary Team | Robust RB | 0.78 |  |  | 12, 29, 32 |  |
| 22 | Denver Pro H2H Points PPR League | 2ptConversion | Robust RB | 0.82 | Late-Round QB | 0.76 | 10, 11 | 110 |
| 23 | Kansas City Pro H2H Points PPR League | PackaPunch | Robust RB | 0.82 |  |  | 1, 20 |  |
| 24 | Washington Pro H2H Points PPR League | Jaylin's Finest Team | Hero RB | 0.87 | Anchor WR | 0.80 | 15, 55 | 6, 26, 35 |
| 25 | Philadelphia Pro H2H Points PPR League | Jaylin's Scary Team | Robust RB | 0.82 | Elite TE | 0.76 | 10, 11 | 30 |
| 26 | Kansas City Pro H2H Points PPR League | Jaylin's Finest Team | Robust RB | 0.82 | Late-Round QB | 0.76 | 8, 13 | 88 |
| 27 | Green Bay Pro H2H Points PPR League | Jaylin's Finest Team | Hero RB | 0.87 |  |  | 15, 55 |  |
| 28 | Chicago Pro H2H Points PPR League | Jaylin's Finest Team | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 4, 17, 24 | 4, 37, 44 |
| 29 | New England Beginner H2H Points PPR League | StoleBalenCiaga | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 5, 16, 25 | 5, 36, 45 |
| 30 | Dallas Pro H2H Points PPR League | ALLCAPSWhenYouSayTheManName | Balanced/BPA | 0.55 | Elite TE | 0.78 | 4, 17, 24 | 24 |
| 31 | Los Angeles Beginner H2H Points PPR League | YourGirlPartOFMyCollection | Robust RB | 0.82 | Late-Round QB | 0.76 | 8, 13 | 108 |
| 32 | Cincinnati Pro H2H Points PPR League | JoeBrrrrrrrrrrrr | Autodraft/Absent | 1.00 |  |  | 1, 20, 21 |  |
| 33 | New Orleans Beginner H2H Points PPR League | ChasingUpside | Robust RB | 0.82 |  |  | 10, 11 |  |
| 34 | Denver Pro H2H Points PPR League | ALLCAPSWhenYouSayTheManName | Autodraft/Absent | 1.00 |  |  | 5, 16, 25 |  |
| 35 | Kansas City Beginner H2H Points PPR League | Xx._CalmWeather_.xx | Robust RB | 0.82 | Late-Round QB | 0.76 | 2, 19 | 119 |
| 36 | Baltimore Pro H2H Points PPR League | EstablishTheRun | Robust RB | 0.82 |  |  | 9, 12 |  |
| 38 | Los Angeles Beginner H2H Points PPR League | AtouchOfTheDowns | Robust RB | 0.82 | Late-Round QB | 0.76 | 1, 20 | 120 |
| 39 | San Francisco Pro H2H Points PPR League | ShankoRainGod | Robust RB | 0.82 | Late-Round QB | 0.76 | 10, 11 | 90 |
| 40 | Carolina Pro H2H Points PPR League | ALLCAPSWhenYouSayTheManName | Robust RB | 0.82 | Late-Round QB | 0.76 | 7, 14 | 114 |
| 41 | Seattle Beginner H2H Points PPR League | ALLCAPSWhenYouSayTheManName | Autodraft/Absent | 1.00 |  |  | 2, 19, 22 |  |
| 42 | Indianapolis Pro H2H Points PPR League | ALLCAPSWhenYouSayTheMAnName | Robust RB | 0.78 | Late-Round QB | 0.76 | 14, 27, 34 | 114 |
| 43 | Philadelphia Pro H2H Points PPR League | UpTheMiddleUpTheMiddle | Robust RB | 0.82 | Late-Round QB | 0.76 | 9, 12 | 109 |
| 44 | Denver Pro H2H Points PPR League | BibliCallyRich | Robust RB | 0.82 |  |  | 7, 14 |  |
| 45 | Chicago Beginner H2H Points PPR League | AJBrownDEEPBALL | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 6, 15, 26 | 6, 26, 46 |
| 46 | Las Vegas Pro H2H Points PPR League | ShankoRains | Robust RB | 0.78 |  |  | 11, 30, 31 |  |
| 47 | Arizona Pro H2H Points PPR League | FroZone | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 2, 19, 22 | 2, 39, 42 |
| 48 | Indianapolis Beginner H2H Points PPR League | AilenDiscoBall | Robust RB | 0.82 | Elite TE | 0.78 | 3, 18 | 23 |
| 49 | Minnesota Pro H2H Points PPR League | ManeeshOnTheBeatSHABANG | Robust RB | 0.82 |  |  | 9, 12 |  |
| 50 | Cincinnati Pro H2H Points PPR League | NoFaceNoCase | Robust RB | 0.82 |  |  | 1, 20 |  |
| 51 | Indianapolis Pro H2H Points PPR League | XxxWeatherMan | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 3, 18, 23 | 3, 38, 43 |
| 52 | Seattle Beginner H2H Points PPR League | KakashiEsaki | Robust RB | 0.82 | Late-Round QB | 0.76 | 10, 11 | 110 |
| 53 | Dallas Pro H2H Points PPR League | XxGolfStreamxX | Robust RB | 0.82 |  |  | 1, 20 |  |
| 54 | Kansas City Pro H2H Points PPR League | MaxADOT | Robust RB | 0.82 | Late-Round QB | 0.76 | 9, 12 | 109 |
| 55 | Seattle Pro H2H Points PPR League | TrickelDownTDnomics | Robust RB | 0.78 | Late-Round QB | 0.76 | 16, 25, 36 | 105 |
| 56 | Denver Gridiron Gauntlet League | 2ndDwnRead | Robust RB | 0.82 | Late-Round QB | 0.76 | 3, 18 | 103 |
| 57 | Atlanta Pro H2H Points PPR League | LeakTheGatoradeColor | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 6, 15, 26 | 6, 26, 46 |
| 58 | Seattle Beginner H2H Points PPR League | RobustRBOreo | Robust RB | 0.82 | Late-Round QB | 0.76 | 7, 14 | 127 |
| 59 | Miami Pro H2H Points PPR League | YacCity | Balanced/BPA | 0.55 | Late-Round QB | 0.76 | 9, 12, 29 | 112 |
| 60 | Philadelphia Pro H2H Points PPR League | PolarOpposites | Hero RB | 0.86 | Anchor WR | 0.80 | 17, 57 | 4, 24, 37 |
| 61 | Arizona Pro H2H Points PPR League | GoBallMerchant | Robust RB | 0.82 |  |  | 9, 12 |  |
| 62 | Houston Gridiron Gauntlet League | 3rdDwnRead | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 6, 15, 26 | 6, 26, 46 |
| 63 | Arizona Pro H2H Points PPR League | TDUltra | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 6, 15, 26 | 6, 26, 46 |
| 64 | New York Pro H2H Points PPR League | QbrMaxxxing | Robust RB | 0.82 | Elite TE | 0.78 | 3, 18 | 23 |
| 65 | Houston Pro H2H Points PPR League | YardsPerAttMaxxxing | Robust RB | 0.82 |  |  | 1, 20 |  |
| 66 | New York Pro H2H Points PPR League | DownFieldMerchant | Robust RB | 0.78 | Late-Round QB | 0.76 | 16, 25, 36 | 116 |
| 67 | Las Vegas Pro H2H Points PPR League | HowBoutThemCowboys | Robust RB | 0.82 | Late-Round QB | 0.76 | 4, 17 | 104 |
| 68 | Cincinnati Pro H2H Points PPR League | VolumeWins | Hero RB | 0.87 | Anchor WR | 0.80 | 15, 55 | 6, 26, 35 |
| 69 | Baltimore Gridiron Gauntlet League | 4thDwnRead | Hero RB | 0.86 | Anchor WR | 0.80 | 17, 64 | 4, 24, 37 |
| 70 | Seattle Pro H2H Points PPR League | SunGodNika | Balanced/BPA | 0.55 | Late-Round QB | 0.76 | 8, 13, 28 | 113 |
| 71 | Baltimore Beginner H2H Points PPR League | CPUFlagPlant | Autodraft/Absent | 1.00 |  |  | 1, 20, 21 |  |
| 72 | Jacksonville Beginner H2H Points PPR League | XxRBTriniTDy | Robust RB | 0.78 | Late-Round QB | 0.76 | 17, 24, 37 | 157 |
| 73 | Atlanta Pro H2H Points PPR League | ChasingYPRR | Balanced/BPA | 0.55 |  |  | 5, 16, 25 |  |
| 74 | Philadelphia Pro H2H Points PPR League | GameOfTheCentury | Hero RB | 0.86 | Anchor WR | 0.80 | 18, 58 | 3, 38, 43 |
| 75 | New York Pro H2H Points PPR League | PointOfTheCatch | Robust RB | 0.82 |  |  | 10, 11 |  |
| 76 | Denver Beginner H2H Points PPR League | HailMary | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 8, 13, 28 | 8, 13, 28 |
| 77 | Detroit Pro H2H Points PPR League | TprrMaxxxxing | Hero RB | 0.81 | Anchor WR | 0.80 | 39, 62 | 2, 19, 22 |
| 78 | Washington Pro H2H Points PPR League | PlayAction | Robust RB | 0.78 |  |  | 17, 24, 44 |  |
| 79 | New York Pro H2H Points PPR League | BlameItOntheQB | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 9, 12, 29 | 9, 32, 49 |
| 80 | Chicago Pro H2H Points PPR League | FedQB | Robust RB | 0.82 | Late-Round QB | 0.76 | 10, 11 | 110 |
| 81 | Dallas Pro H2H Points PPR League | NoHuddle  | Balanced/BPA | 0.55 |  |  | 10, 11, 30 |  |
| 82 | New England Pro H2H Points PPR League | Spiral | Robust RB | 0.82 |  |  | 1, 20 |  |
| 83 | Seattle Pro H2H Points PPR League | ThreeTechnique    | Hero RB | 0.81 | Anchor WR | 0.80 | 38, 58 | 3, 18, 43 |
| 84 | Atlanta Pro H2H Points PPR League | 2WrSets | Robust RB | 0.82 |  |  | 5, 16 |  |
| 85 | Kansas City Pro H2H Points PPR League | WinRate | Hero RB | 0.80 | Anchor WR | 0.80 | 43, 58 | 3, 18, 38 |
| 86 | Miami Pro H2H Points PPR League | BlameTheOLine | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 3, 18, 23 | 3, 23, 43 |
| 87 | Atlanta Pro H2H Points PPR League | CheckDownMerchant | Robust RB | 0.82 |  |  | 9, 12 |  |
| 88 | Jacksonville Gridiron Gauntlet League | FptsOverExspected | Robust RB | 0.82 |  |  | 2, 19 |  |
| 89 | Las Vegas Pro H2H Points PPR League | SmartMoney | Hero RB | 0.88 | Anchor WR | 0.80 | 12, 72 | 9, 29, 49 |
| 90 | Chicago Pro H2H Points PPR League | WalkEmDown | Robust RB | 0.82 | Late-Round QB | 0.76 | 2, 19 | 119 |
| 91 | Chicago Pro H2H Points PPR League | OnSidekick | Robust RB | 0.82 |  |  | 1, 20 |  |
| 92 | New York Beginner H2H Points PPR League | MaxADOT | Hero RB | 0.87 | Anchor WR | 0.80 | 15, 55 | 6, 26, 35 |
| 93 | San Francisco Pro H2H Points PPR League | 3WrSets | Hero RB | 0.89 |  |  | 6, 55 |  |
| 95 | Seattle Pro H2H Points PPR League | GoalLine | Robust RB | 0.82 | Elite TE | 0.79 | 1, 20 | 21 |
| 96 | Jacksonville Pro H2H Points PPR League | SetHut | Balanced/BPA | 0.55 |  |  | 4, 17, 24 |  |
| 97 | Dallas Pro H2H Points PPR League | BigCountryOT | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 4, 17, 24 | 4, 24, 44 |
| 98 | Dallas Pro H2H Points PPR League | HighXFP | Robust RB | 0.82 | Late-Round QB | 0.76 | 10, 11 | 91 |
| 99 | Washington Pro H2H Points PPR League | CallapseThePocket | Hero RB | 0.86 | Anchor WR | 0.80 | 19, 59 | 2, 22, 39 |
| 100 | Los Angeles Pro H2H Points PPR League | BULLRUSH | Robust RB | 0.82 | Late-Round QB | 0.76 | 2, 19 | 122 |
| 101 | Miami Gridiron Gauntlet League | BlameItOntheDL | Robust RB | 0.82 | Late-Round QB | 0.76 | 7, 14 | 107 |
| 102 | Los Angeles Gridiron Gauntlet League | DeepBalll | Robust RB | 0.82 |  |  | 2, 19 |  |
| 103 | Arizona Pro H2H Points PPR League | BlameItOntheRB | Balanced/BPA | 0.55 | Anchor WR | 0.80 | 7, 14, 27 | 7, 34, 47 |
| 104 | Minnesota Pro H2H Points PPR League | WheelRoute | Robust RB | 0.82 |  |  | 5, 16 |  |
| 105 | Las Vegas Pro H2H Points PPR League | Unsolicited Nix Pics | Hero RB | 0.86 | Anchor WR | 0.80 | 16, 56 | 5, 25, 36 |
| 106 | Baltimore Pro H2H Points PPR League | Casey Anthony's Daycare | Robust RB | 0.82 |  |  | 1, 20 |  |
| 107 | Carolina Pro H2H Points PPR League | TushPush | Robust RB | 0.78 |  |  | 17, 24, 44 |  |
| 108 | Denver Pro H2H Points PPR League |  Nabers Think I’m Selling Dope | Hero RB | 0.87 | Anchor WR | 0.80 | 13, 53 | 8, 28, 33 |
| 109 | New York Pro H2H Points PPR League | Shinya's Smart Team | Autodraft/Absent | 1.00 |  |  | 6, 15, 26 |  |
| 110 | Washington Pro H2H Points PPR League | IntialRead | Robust RB | 0.82 | Late-Round QB | 0.76 | 3, 18 | 98 |
| 111 | Los Angeles Pro H2H Points PPR League | Shinya's Smart Team | Hero RB | 0.86 | Elite TE | 0.78 | 18, 58 | 23 |
| 112 | Atlanta Pro H2H Points PPR League | Shinya's Smart Team | Robust RB | 0.82 | Late-Round QB | 0.76 | 2, 19 | 82 |
| 113 | Cleveland Pro H2H Points PPR League | Shinya's Smart Team | Balanced/BPA | 0.55 |  |  | 9, 12, 29 |  |
| 114 | Atlanta Pro H2H Points PPR League | Shinya's Smart Team | Robust RB | 0.82 |  |  | 2, 19 |  |
