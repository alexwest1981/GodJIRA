import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : AutoCore — the flow: the code's leftovers
// Nodes   : 8  |  Connections: 6
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// Note                               stickyNote
// EveryTwelveHours                   scheduleTrigger
// ManualTrigger                      manualTrigger
// Config                             set
// Step1TheScan                       executeCommand
// Step2TheCodeMap                    executeCommand
// Summary                            code
// ReportToThePanel                   httpRequest                [onError→regular]
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// EveryTwelveHours
//    → Config
//      → Step1TheScan
//        → Step2TheCodeMap
//          → Summary
//            → ReportToThePanel
// ManualTrigger
//    → Config (↩ loop)
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'Dv5T1dBjhb1gmiV6',
    name: "AutoCore — the flow: the code's leftovers",
    active: true,
    isArchived: false,
    settings: { executionOrder: 'v1' },
})
export class AutocoreTheFlowTheCodeSLeftoversWorkflow {
    // =====================================================================
    // CONFIGURATION DES NOEUDS
    // =====================================================================

    @node({
        id: 'b3f1c6a2-58d4-4a17-9e02-6c7d1f8b4a90',
        name: 'Note',
        type: 'n8n-nodes-base.stickyNote',
        version: 1,
        position: [-560, 112],
    })
    Note = {
        content: `## Read-only — the code's side

Answers the other half of the question: not what is on the board, but **what the code is
carrying**. The scan pairs every issue with the files it names; this run counts what is
left over both ways — files no issue names, and issues that name no file — and reads how
the code itself hangs together (modules, connections, languages).

Nothing here writes on the board.

The commands carry no project key: the CLI takes it from the repo link, so this run follows
whichever project this repo is linked to. The last node hands the reading to the panel
(POST /api/automation) — that is what the hub shows as "Projektet du är kopplad till".`,
        height: 340,
        width: 460,
        color: 4,
    };

    @node({
        id: 'c47a9e10-2b86-4d5f-8a31-0e94b6c2d7f5',
        name: 'Every twelve hours',
        type: 'n8n-nodes-base.scheduleTrigger',
        version: 1.4,
        position: [-64, 464],
    })
    EveryTwelveHours = {
        rule: {
            interval: [
                {
                    field: 'hours',
                    hoursInterval: 12,
                    triggerAtMinute: 23,
                },
            ],
        },
    };

    @node({
        id: 'd5e8b7c3-9014-4f2a-b6d8-3a17c5e9024b',
        name: 'Manual Trigger',
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [-80, 208],
    })
    ManualTrigger = {};

    @node({
        id: 'e61d0f84-3c7b-4a59-9d02-8b46f1a3c7e8',
        name: 'Config',
        type: 'n8n-nodes-base.set',
        version: 3.4,
        position: [144, 208],
    })
    Config = {
        assignments: {
            assignments: [
                {
                    id: 'f2a7c19d-6b04-4e83-a5c1-7d92e0b64f13',
                    name: 'repo',
                    value: "={{ $env.GODJIRA_REPO || $env.HOME + '/Projects/godjira' }}",
                    type: 'string',
                },
                {
                    id: 'a9c35e72-1d68-4b90-8f27-5c0e6a1b93d4',
                    name: 'panel',
                    value: 'http://127.0.0.1:8788',
                    type: 'string',
                },
            ],
        },
        options: {},
    };

    @node({
        id: 'b70e2a45-9c13-4d86-a27f-6e01b9c5d382',
        name: 'Step 1 - the scan',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [368, 128],
    })
    Step1TheScan = {
        command: "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow scan --json",
    };

    @node({
        id: 'c8f4d16b-7a29-4e35-b8c0-2f97a3e6d541',
        name: 'Step 2 - the code map',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [368, 320],
    })
    Step2TheCodeMap = {
        command: "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh codemap --json",
    };

    @node({
        id: 'd94b6e07-5f31-4c28-9a7e-1b60d2f8c53a',
        name: 'Summary',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [624, 208],
    })
    Summary = {
        jsCode: `const read = (json) => {
  try { return JSON.parse(json.stdout || '{}'); } catch (e) { return { exitCode: null, payload: null, raw: json.stdout || '' }; }
};
const scan = read($('Step 1 - the scan').first().json);
const code = read($('Step 2 - the code map').first().json);
const theMap = scan.payload || null;
const theCode = code.payload || null;
const firstLine = (value) => String(value || '').split(String.fromCharCode(10)).filter(l => l.trim())[0] || '';
const langs = ((theCode || {}).languages || []).map(l => l.name + ' ' + l.files).join(', ');

let note;
if (!theMap) note = 'the scan could not answer: ' + firstLine(scan.raw);
else {
  const silent = theMap.silentCount || 0;
  const loose = theMap.unmapped || 0;
  note = silent + ' of ' + theMap.files + ' files are named by no issue, and ' +
    loose + ' issues name no file' +
    (langs ? ' — the code is ' + langs : '');
  if (!theCode) note += '. The code map did not answer: ' + firstLine(code.raw);
}

return [{ json: { checkedAt: new Date().toISOString(),
  project: (theMap || {}).project || '', projectSource: (theMap || {}).projectSource || '',
  runner: 'n8n', workflowId: $workflow.id,
  ok: Boolean(theMap) && Boolean(theCode), note: note, amIOn: null,
  theFlowsPick: null, runnersUp: [], theFlowsOwnJournal: [],
  theMap: theMap ? { issues: theMap.issues, files: theMap.files, mapped: theMap.mapped,
    missing: theMap.unmapped, silentFiles: theMap.silentCount, at: theMap.at } : null,
  theCode: theCode ? { modules: theCode.packages, connections: theCode.edgesCount,
    files: theCode.files, languages: langs, at: new Date().toISOString() } : null } }];`,
    };

    @node({
        id: 'e15c7a92-8b46-4d70-a3f9-2c58e0b16d47',
        name: 'Report to the panel',
        type: 'n8n-nodes-base.httpRequest',
        version: 4.2,
        position: [888, 208],
        onError: 'continueRegularOutput',
    })
    ReportToThePanel = {
        method: 'POST',
        url: "={{ $('Config').first().json.panel }}/api/automation",
        sendBody: true,
        specifyBody: 'json',
        jsonBody: '={{ JSON.stringify($json) }}',
        options: {
            timeout: 30000,
            response: {
                response: {
                    neverError: true,
                },
            },
        },
    };

    // =====================================================================
    // ROUTAGE ET CONNEXIONS
    // =====================================================================

    @links()
    defineRouting() {
        this.ManualTrigger.out(0).to(this.Config.in(0));
        this.EveryTwelveHours.out(0).to(this.Config.in(0));
        this.Config.out(0).to(this.Step1TheScan.in(0));
        this.Step1TheScan.out(0).to(this.Step2TheCodeMap.in(0));
        this.Step2TheCodeMap.out(0).to(this.Summary.in(0));
        this.Summary.out(0).to(this.ReportToThePanel.in(0));
    }
}
