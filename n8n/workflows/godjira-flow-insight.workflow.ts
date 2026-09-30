import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : GodJIRA — the flow: insight
// Nodes   : 10  |  Connections: 8
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// Note                               stickyNote
// EveryHour                          scheduleTrigger
// ManualTrigger                      manualTrigger
// Config                             set
// Step1Current                       executeCommand
// Step2ThePickDryRun                 executeCommand
// Step3TheJournal                   executeCommand
// Step4TheScan                      executeCommand
// Summary                            code
// ReportToThePanel                   httpRequest                [onError→regular]
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// EveryHour
//    → Config
//      → Step1Current
//        → Step2ThePickDryRun
//          → Step3TheJournal
//            → Step4TheScan
//              → Summary
//                → ReportToThePanel
// ManualTrigger
//    → Config (↩ loop)
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'Drq6dySctqkoGTPj',
    name: 'GodJIRA — the flow: insight',
    active: true,
    isArchived: false,
    settings: { executionOrder: 'v1', binaryMode: 'separate' },
})
export class GodjiraTheFlowInsightWorkflow {
    // =====================================================================
    // CONFIGURATION DES NOEUDS
    // =====================================================================

    @node({
        id: '5f853340-5d1c-40e5-a6da-267c7ca606ea',
        name: 'Note',
        type: 'n8n-nodes-base.stickyNote',
        version: 1,
        position: [-560, 112],
    })
    Note = {
        content: `## Read-only
Runs the flow's own commands and collects what they answer: what you are on, what the flow would take next, and the flow's own write journal.

Nothing here writes on the board.

The commands carry no project key: the CLI takes it from the repo link, so this run follows whichever project you are linked to. The last node hands the reading to the panel (POST /api/automation) — that is what the hub shows as "Projektet du är kopplad till".`,
        height: 300,
        width: 460,
        color: 4,
    };

    @node({
        id: 'a8d0b6a4-6c1f-4c2e-9f2e-7b6f2a2f0c11',
        name: 'Every hour',
        type: 'n8n-nodes-base.scheduleTrigger',
        version: 1.4,
        position: [-64, 464],
    })
    EveryHour = {
        rule: {
            interval: [
                {
                    field: 'hours',
                    hoursInterval: 1,
                    triggerAtMinute: 17,
                },
            ],
        },
    };

    @node({
        id: '1006d6f1-9976-48a6-839f-0cdf2077cdc6',
        name: 'Manual Trigger',
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [-80, 208],
    })
    ManualTrigger = {};

    @node({
        id: '19939679-c25c-4318-aa9c-3f38f53e67de',
        name: 'Config',
        type: 'n8n-nodes-base.set',
        version: 3.4,
        position: [144, 208],
    })
    Config = {
        assignments: {
            assignments: [
                {
                    id: '57df65fa-26d0-4a09-94ab-72d48519ddef',
                    name: 'repo',
                    value: '/home/alex/Projects/godjira',
                    type: 'string',
                },
                {
                    id: '9c2f1b40-7a55-4d3e-8f61-2ab6c0d91e77',
                    name: 'panel',
                    value: 'http://127.0.0.1:8788',
                    type: 'string',
                },
            ],
        },
        options: {},
    };

    @node({
        id: '7192c1d2-7ed0-4136-b31d-a5a94f370290',
        name: 'Step 1 - current',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [368, 80],
    })
    Step1Current = {
        command: "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow current",
    };

    @node({
        id: '8b16a1e6-ee39-41e9-9648-a299b0da6a33',
        name: 'Step 2 - the pick (dry run)',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [368, 240],
    })
    Step2ThePickDryRun = {
        command: "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow next --dry-run --json",
    };

    @node({
        id: 'c8f51a22-717a-4422-80b0-8c69ba36ee90',
        name: 'Step 3 - the journal',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [368, 400],
    })
    Step3TheJournal = {
        command: "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh bridge journal 20",
    };

    // Kartan över projektet: samma kommando som knappen i panelen kör, och samma
    // karta. Den skriver bara filen scannen äger (inget på tavlan), så den hör hemma
    // i läse-flödet -- och den är färsk varje timme utan att någon trycker.
    @node({
        id: '5e3a71c8-2b64-4d19-8f77-9a0c1e5b7d32',
        name: 'Step 4 - the scan',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [496, 464],
    })
    Step4TheScan = {
        command: "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow scan --json",
    };

    @node({
        id: '10bbc75d-3141-4e69-bf95-f2b0a5534aa1',
        name: 'Summary',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [624, 240],
    })
    Summary = {
        jsCode: `const read = (json) => {
  try { return JSON.parse(json.stdout || '{}'); } catch (e) { return { exitCode: null, payload: null, raw: json.stdout || '' }; }
};
const cfg = $('Config').first().json;
const cur = read($('Step 1 - current').first().json);
const pick = read($('Step 2 - the pick (dry run)').first().json);
const journal = read($('Step 3 - the journal').first().json);
const scan = read($('Step 4 - the scan').first().json);
const theMap = scan.payload || null;

const p = pick.payload || {};
const candidate = p.wouldTake || p.proposal || null;

const failed = [cur, pick, journal].find(r => r.payload === null && String(r.raw || '').trim());
const firstLine = (value) => String(value || '').split(String.fromCharCode(10)).filter(l => l.trim())[0] || '';

let note;
if (failed) note = 'the flow could not answer: ' + firstLine(failed.raw);
else if (p.wouldTake) note = 'the flow would take ' + candidate.key + ' for ' + p.assignTo + ' and move it to ' + p.status;
else if (p.proposal) note = candidate.key + " is someone else's; the panel needs a second press on it, this run writes nothing";
else if (pick.exitCode === 1) note = 'nothing not-started to take';
else note = 'the flow answered with exit code ' + pick.exitCode + ': ' + firstLine(pick.raw);

const runnersUp = (p.skipped || []).slice(0, 5).map(s => s.key + ' [' + (s.priority || '-') + '] ' + s.summary);
const log = ((journal.payload || {}).log || []).slice(0, 10)
  .map(e => (e.at + ' ' + e.action + ' ' + (e.key || '-') + ' ' + (e.ok ? 'ok' : 'FAILED') + ' ' + (e.detail || '')).trim());

// The project key is the CLI's own answer: the repo link decided it, not this flow.
return [{ json: { checkedAt: new Date().toISOString(), project: p.project || '',
  projectSource: p.projectSource || '', runner: 'n8n', workflowId: $workflow.id,
  ok: !failed, note: note, amIOn: firstLine(cur.raw) || null, theFlowsPick: candidate,
  runnersUp: runnersUp, theFlowsOwnJournal: log,
  theMap: theMap ? { issues: theMap.issues, files: theMap.files, mapped: theMap.mapped,
    missing: theMap.unmapped, silentFiles: theMap.silentCount, at: theMap.at } : null } }];`,
    };

    @node({
        id: 'd4b0f7c1-6a2e-4d8b-9c33-51e7a9f0b2d4',
        name: 'Report to the panel',
        type: 'n8n-nodes-base.httpRequest',
        version: 4.2,
        position: [888, 240],
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
        this.EveryHour.out(0).to(this.Config.in(0));
        this.Config.out(0).to(this.Step1Current.in(0));
        this.Step1Current.out(0).to(this.Step2ThePickDryRun.in(0));
        this.Step2ThePickDryRun.out(0).to(this.Step3TheJournal.in(0));
        this.Step3TheJournal.out(0).to(this.Step4TheScan.in(0));
        this.Step4TheScan.out(0).to(this.Summary.in(0));
        this.Summary.out(0).to(this.ReportToThePanel.in(0));
    }
}
