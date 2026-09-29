import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : GodJIRA — the flow: take the next critical
// Nodes   : 9  |  Connections: 7
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// Note                               stickyNote
// ManualTrigger                      manualTrigger
// Config                             set
// Step1ThePickDryRun                 executeCommand
// ReadThePick                        code
// IsThereAKeyOfYourOwn               if
// Step2TakeExactlyThatKey            executeCommand
// VerifyTheSiteSaidSo                code
// NothingTaken                       noOp
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// ManualTrigger
//    → Config
//      → Step1ThePickDryRun
//        → ReadThePick
//          → IsThereAKeyOfYourOwn
//            → Step2TakeExactlyThatKey
//              → VerifyTheSiteSaidSo
//           .out(1) → NothingTaken
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'cz1S4nXkfWffj2Wp',
    name: 'GodJIRA — the flow: take the next critical',
    active: false,
    isArchived: false,
    settings: { executionOrder: 'v1' },
})
export class GodjiraTheFlowTakeTheNextCriticalWorkflow {
    // =====================================================================
    // CONFIGURATION DES NOEUDS
    // =====================================================================

    @node({
        id: 'd0482a46-d7bc-4d50-9199-5f16cea28911',
        name: 'Note',
        type: 'n8n-nodes-base.stickyNote',
        version: 1,
        position: [-560, 60],
    })
    Note = {
        content: `## This one writes
It takes the most critical not-started item, assigns you and moves it to *In Progress* — on a board you share with the group.

It takes **exactly the key step 1 showed**, never whatever is on top when step 2 runs, and only work that is yours or nobody's. Someone else's issue is never taken here.

One press of *Execute workflow* is the whole gesture. The panel still needs two; use it when you want that guard.`,
        height: 300,
        width: 470,
        color: 3,
    };

    @node({
        id: '6d923cd6-0947-4124-854e-14d3c3cf41e0',
        name: 'Manual Trigger',
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [-80, 180],
    })
    ManualTrigger = {};

    @node({
        id: '2f2c671d-128b-4192-8109-d4efa6dc212d',
        name: 'Config',
        type: 'n8n-nodes-base.set',
        version: 3.4,
        position: [140, 180],
    })
    Config = {
        assignments: {
            assignments: [
                {
                    id: 'c24a9475-c5c0-4f22-b80b-d1732367d398',
                    name: 'repo',
                    value: '/home/alex/Projects/godjira',
                    type: 'string',
                },
                {
                    id: '14afb972-0970-4d46-b141-65214350fbac',
                    name: 'project',
                    value: 'SCRUM',
                    type: 'string',
                },
            ],
        },
        options: {},
    };

    @node({
        id: 'fb0ec137-bc21-4eaf-9017-f8497457c365',
        name: 'Step 1 - the pick (dry run)',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [360, 180],
    })
    Step1ThePickDryRun = {
        command:
            "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow next --dry-run --json --project {{ $('Config').first().json.project }}",
        executeOnce: true,
    };

    @node({
        id: '055af973-db41-42b4-95d7-2e7966055331',
        name: 'Read the pick',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [600, 180],
    })
    ReadThePick = {
        mode: 'runOnceForAllItems',
        language: 'javaScript',
        jsCode: `const read = (json) => {
  try { return JSON.parse(json.stdout || '{}'); } catch (e) { return { exitCode: null, payload: null, raw: json.stdout || '' }; }
};
const pick = read($('Step 1 - the pick (dry run)').first().json);
const p = pick.payload || {};
const own = p.wouldTake || null;      // mine or nobody's - the flow may take it
const other = p.proposal || null;     // someone else's - the panel's second press owns that
let reason = '';
if (!own) {
  reason = other
    ? other.key + " is someone else's. Take it over from the panel, where the second press names the key."
    : (p.error || 'nothing not-started to take');
}
return [{ json: { key: own ? own.key : '', wouldTake: own, reason: reason } }];`,
    };

    @node({
        id: '819058c5-c0d1-460e-a32d-5d746b39a8f3',
        name: 'Is there a key of your own?',
        type: 'n8n-nodes-base.if',
        version: 2.3,
        position: [840, 200],
    })
    IsThereAKeyOfYourOwn = {
        conditions: {
            options: {
                caseSensitive: true,
                leftValue: '',
                typeValidation: 'strict',
                version: 2,
            },
            conditions: [
                {
                    id: 'a6e007fc-ce92-4b3d-9a1e-989670abe93a',
                    leftValue: '={{ $json.key }}',
                    rightValue: '',
                    operator: {
                        type: 'string',
                        operation: 'notEmpty',
                        singleValue: true,
                    },
                },
            ],
            combinator: 'and',
        },
        looseTypeValidation: false,
        options: {},
    };

    @node({
        id: 'e4a29ddc-3869-43f5-a5ba-b569dcbfaef9',
        name: 'Step 2 - take exactly that key',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [1080, 100],
    })
    Step2TakeExactlyThatKey = {
        command:
            "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh flow next --json --expect \"{{ $json.key }}\" --project {{ $('Config').first().json.project }}",
        executeOnce: true,
    };

    @node({
        id: '00936591-ff9c-406b-9756-e28c7a14d1b0',
        name: 'Verify - the site said so',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [1320, 100],
    })
    VerifyTheSiteSaidSo = {
        mode: 'runOnceForAllItems',
        language: 'javaScript',
        jsCode: `const read = (json) => {
  try { return JSON.parse(json.stdout || '{}'); } catch (e) { return { exitCode: null, payload: null, raw: json.stdout || '' }; }
};
const r = read($('Step 2 - take exactly that key').first().json);
const p = r.payload || {};
const t = p.took || null;
return [{ json: {
  wrote: !!p.ok,
  took: t,
  detail: p.ok ? (t.key + ' - ' + t.assignee + ' - ' + t.status) : (p.error || String(r.raw || '').split('\\n')[0]),
  note: p.ok
    ? 'the CLI read the write back from the site before it said ok'
    : ('the flow exited ' + r.exitCode + ' and nothing counts as written'),
} }];`,
    };

    @node({
        id: '22056afd-ad31-47b3-affa-9b211bd0d2e3',
        name: 'Nothing taken',
        type: 'n8n-nodes-base.noOp',
        version: 1,
        position: [1080, 320],
    })
    NothingTaken = {};

    // =====================================================================
    // ROUTAGE ET CONNEXIONS
    // =====================================================================

    @links()
    defineRouting() {
        this.ManualTrigger.out(0).to(this.Config.in(0));
        this.Config.out(0).to(this.Step1ThePickDryRun.in(0));
        this.Step1ThePickDryRun.out(0).to(this.ReadThePick.in(0));
        this.ReadThePick.out(0).to(this.IsThereAKeyOfYourOwn.in(0));
        this.IsThereAKeyOfYourOwn.out(0).to(this.Step2TakeExactlyThatKey.in(0));
        this.IsThereAKeyOfYourOwn.out(1).to(this.NothingTaken.in(0));
        this.Step2TakeExactlyThatKey.out(0).to(this.VerifyTheSiteSaidSo.in(0));
    }
}
