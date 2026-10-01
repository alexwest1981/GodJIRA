# GodJIRA i n8n

Flödet i en tavla man kan titta på i stället för att gissa. n8n körs på den här
maskinen och anropar **samma `bin/jira_flow.py` och `bin/jira_bridge.py` som
panelen och MCP-servern** — en flödesväg, två fönster. Ingen logik är kopierad
hit, så tavlan kan aldrig visa en annan sanning än den panelen skriver.

## Körningen

| | |
|---|---|
| Adress | http://127.0.0.1:5678 (bara localhost) |
| Tjänst | `systemctl --user status n8n` · `restart` · `stop` |
| Enhet | `~/.config/systemd/user/n8n.service` — kopian i `n8n/n8n.service` är samma fil (startar vid inloggning, `loginctl enable-linger` är satt) |
| Node | 22.23.3 ur mise — **inte** maskinens 26: n8n:s egna beroenden bygger en nativ modul utan färdig binär för 26, och ett misslyckat bygge tar hela den globala installationen med sig. I skalet är `n8n` en rad i `~/.local/bin/n8n` som pekar dit |
| Ägarlösenord | `secret-tool lookup service n8n-godjira account owner` |
| API-nyckel | `secret-tool lookup service n8n-godjira account api-key` (samma som `n8nac` använder) |

`NODES_EXCLUDE=[]` i enheten: n8n v2 stänger av exakt två noder som standard
(Execute Command och Local File Trigger). Flödet *är* ett kommando på den här
maskinen, bredvid credentialen, och instansen lyssnar bara på localhost.

## Flödena

| Fil | Vad den gör |
|---|---|
| `workflows/godjira-flow-insight.workflow.ts` | **Läser.** Vad du är på nu, vad flödet skulle ta härnäst (torrkörning), och flödets egen journal. Skriver ingenting. Går dessutom **varje vardag 07:30** av sig själv — den är publicerad och aktiv, så historiken fylls på utan att du trycker. |
| `workflows/godjira-flow-take-next.workflow.ts` | **Skriver.** Tar det mest kritiska opåbörjade ärendet, assignar dig och sätter *In Progress*. Tar exakt den nyckeln som steg 1 visade, och aldrig någon annans ärende. |

Kör med **Execute workflow** i editorn. Skrivflödet har ingen trigger som gör något
av sig själv — det ska tryckas på, och bara när du menar det. Läseflödet har en
klocka (ovan) och en manuell start.

Publicering är det som gör att klockan går: `n8nac workflow activate <id>` tar upp
det i n8ns schemaläggare, `n8nac workflow deactivate <id>` tar ner det. Ett aktivt
flöde förblir aktivt över omstart.

## `bin/flow-call.sh`

n8n:s Execute Command-kastar bort stdout när kommandot ger felkod, och det här
flödet *svarar* med felkoden (1 = inget att ta, 2 = fel, 3 = någon annans ärende
kräver ett andra tryck). Skriptet lägger därför koden i svaret och går alltid ut
0:

```json
{"exitCode": 0, "payload": {"ok": true, "wouldTake": {"key": "SCRUM-101"}}, "raw": "..."}
```

Noderna läser `stdout` och tolkar den strängen. `payload` är CLI:ts JSON, eller
`null` när kommandot skrev rader i stället (`current`, `journal`).

## Ändra i dem

`.workflow.ts` är källan, inte det som ligger i databasen:

```sh
n8nac list                          # vad som är spårat och vad som skiljer
n8nac push n8n/workflows/<fil> --verify
n8nac pull <workflow-id>            # om något ändrades i editorn
```

## Ett flöde för ett annat repo

Panelen ritar varje `workflows/*.workflow.ts` som finns här — men bara i det repo flödet
hör till. AutoCore har inget eget flöde än, alltså står dess Automatik-tabb tom med en not
om varför. Så gör man ett:

```sh
cp n8n/workflows/github-what-needs-me.workflow.ts n8n/workflows/autocore-<vad den gör>.workflow.ts
#   byt namn, noder och vad den gör -- och ge den ett NYTT id: `uuidgen` i id-raden i
#   @workflow. Id:t är nyckeln till flödet i n8n, så ett kvarglömt id från kopian skriver
#   över originalet i stället för att skapa ett nytt. Typen kräver att raden finns.
n8nac push n8n/workflows/autocore-<vad den gör>.workflow.ts --verify
```

Sedan hemvisten, i `workflows/.scopes.json`:

```json
{ "autocore-<vad den gör>.workflow.ts": "AutoCore" }
```

Utan post visas flödet i **alla** repon; med post bara i sitt eget. Provat: en ny fil
plockas upp av panelen direkt (den ritas ur filen), och `id`-raden är den som avgör om
push skapar eller skriver över.

## Nya maskiner

```sh
npm install -g n8nac n8n
n8n                                 # första gången: gör ägarkontot i webbläsaren
n8nac env add Local --base-url http://127.0.0.1:5678 --workflows-path n8n/workflows
n8nac env auth set Local --api-key-stdin   # nyckeln ur n8n: Settings → API keys
n8nac env use Local
n8nac pull <workflow-id>            # eller: push, se ovan
```

`n8nac skills validate <fil>` fångar schemat men **inte** JavaScripten i en Code-nod.
Efter en ändring i en jsCode: `n8nac convert <fil> --format json -o /tmp/w.json` och
kör `node --check` på varje `jsCode`-fält. Det är där en `\n` som blir en riktig
radbrytning smäller.

`n8nac-config.json` (miljöerna) och `n8n-workflows.d.ts`/`tsconfig.json` går i
git. API-nycklar, n8n-managers butik, Docker-state och `.n8n-sync-events.jsonl`
gör det inte.
