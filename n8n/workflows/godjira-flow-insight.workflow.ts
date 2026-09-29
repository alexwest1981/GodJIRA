import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : GodJIRA — the flow: insight
// Nodes   : 8  |  Connections: 6
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// Note                               stickyNote
// EveryWeekdayAt0730                 scheduleTrigger
// ManualTrigger                      manualTrigger
// Config                             set
// Step1Current                       executeCommand
// Step2ThePickDryRun                 executeCommand
// Step3TheJournal                    executeCommand
// Summary                            code
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// EveryWeekdayAt0730
//    → Config
//      → Step1Current
//        → Step2ThePickDryRun
//          → Step3TheJournal
//            → Summary
// ManualTrigger
//    → Config (↩ loop)
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'Drq6dySctqkoGTPj',
    name: 'GodJIRA — the flow: insight',
    active: false,
    isArchived: false,
    settings: { executionOrder: 'v1' },
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
        position: [-560, 100],
    })
    Note = {
        content: `## Read-only
Runs the flow's own commands and collects what they answer: what you are on, what the flow would take next, and the flow's own write journal.

Nothing here writes on the board.

The three commands are the same code the bar panel and the MCP server call — one flow, two windows.`,
        height: 260,
        width: 460,
        color: 4,
    };

    @node({
        id: 'a8d0b6a4-6c1f-4c2e-9f2e-7b6f2a2f0c11',
        name: 'Every weekday at 07:30',
        type: 'n8n-nodes-base.scheduleTrigger',
        version: 1.4,
        position: [-380, 380],
    })
    EveryWeekdayAt0730 = {
        rule: {
            interval: [
                {
                    field: 'weeks',
                    weeksInterval: 1,
                    triggerAtDay: [1, 2, 3, 4, 5],
                    triggerAtHour: 7,
                    triggerAtMinute: 30,
                },
            ],
        },
    };

    @node({
        id: '1006d6f1-9976-48a6-839f-0cdf2077cdc6',
        name: 'Manual Trigger',
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [-80, 200],
    })
    ManualTrigger = {};

    @node({
        id: '19939679-c25c-4318-aa9c-3f38f53e67de',
        name: 'Config',
        type: 'n8n-nodes-base.set',
        version: 3.4,
        position: [140, 200],
    })
    Config = {
        assignments: {
            assignments: [
                {
                    id: '57df65fa-26d0-4a09-94ab-72d48519ddef',
                    name: 'repo',
                    value: '/home/alex/.config/omarchy/plugins/custom.jira',
                    type: 'string',
                },
                {
                    id: 'ada71d47-f6e5-4a68-bd23-0a002611a909',
                    name: 'project',
                    value: 'SCRUM',
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
        position: [360, 80],
    })
    Step1Current = {
        command:
            "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow current --project {{ $('Config').first().json.project }}",
        executeOnce: true,
    };

    @node({
        id: '8b16a1e6-ee39-41e9-9648-a299b0da6a33',
        name: 'Step 2 - the pick (dry run)',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [360, 240],
    })
    Step2ThePickDryRun = {
        command:
            "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow next --dry-run --json --project {{ $('Config').first().json.project }}",
        executeOnce: true,
    };

    @node({
        id: 'c8f51a22-717a-4422-80b0-8c69ba36ee90',
        name: 'Step 3 - the journal',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [360, 400],
    })
    Step3TheJournal = {
        command: "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh bridge journal 20",
        executeOnce: true,
    };

    @node({
        id: '10bbc75d-3141-4e69-bf95-f2b0a5534aa1',
        name: 'Summary',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [620, 240],
    })
    Summary = {
        mode: 'runOnceForAllItems',
        language: 'javaScript',
        jsCode: `const read = (json) => {
  try { return JSON.parse(json.stdout || '{}'); } catch (e) { return { exitCode: null, payload: null, raw: json.stdout || '' }; }
};
const cfg = $('Config').first().json;
const cur = read($('Step 1 - current').first().json);
const pick = read($('Step 2 - the pick (dry run)').first().json);
const journal = read($('Step 3 - the journal').first().json);

const p = pick.payload || {};
const candidate = p.wouldTake || p.proposal || null;

const failed = [cur, pick, journal].find(r => r.payload === null && String(r.raw || '').trim());

let note;
if (failed) note = 'the flow could not answer: ' + String(failed.raw).trim().split('\\n')[0];
else if (p.wouldTake) note = 'the flow would take ' + candidate.key + ' for ' + p.assignTo + ' and move it to ' + p.status;
else if (p.proposal) note = candidate.key + " is someone else's; the panel needs a second press on it, this run writes nothing";
else if (pick.exitCode === 1) note = 'nothing not-started to take';
else note = 'the flow answered with exit code ' + pick.exitCode + ': ' + String(pick.raw || '').split('\\n')[0];

const runnersUp = (p.skipped || []).slice(0, 5).map(s => s.key + ' [' + (s.priority || '-') + '] ' + s.summary);
const log = ((journal.payload || {}).log || []).slice(0, 10)
  .map(e => (e.at + ' ' + e.action + ' ' + (e.key || '-') + ' ' + (e.ok ? 'ok' : 'FAILED') + ' ' + (e.detail || '')).trim());

return [{ json: { checkedAt: new Date().toISOString(), project: cfg.project,
  note: note, amIOn: String(cur.raw || '').trim() || null, theFlowsPick: candidate,
  runnersUp: runnersUp, theFlowsOwnJournal: log } }];`,
    };

    // =====================================================================
    // ROUTAGE ET CONNEXIONS
    // =====================================================================

    @links()
    defineRouting() {
        this.ManualTrigger.out(0).to(this.Config.in(0));
        this.EveryWeekdayAt0730.out(0).to(this.Config.in(0));
        this.Config.out(0).to(this.Step1Current.in(0));
        this.Step1Current.out(0).to(this.Step2ThePickDryRun.in(0));
        this.Step2ThePickDryRun.out(0).to(this.Step3TheJournal.in(0));
        this.Step3TheJournal.out(0).to(this.Summary.in(0));
    }
}
