import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : GitHub — what needs me
// Nodes   : 7  |  Connections: 5
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// Note                               stickyNote
// ManualTrigger                      manualTrigger
// Config                             set
// Step1MyOpenPullRequests            executeCommand
// Step2WaitingForMyReview            executeCommand
// Step3IssuesAssignedToMe            executeCommand
// Summary                            code
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// ManualTrigger
//    → Config
//      → Step1MyOpenPullRequests
//        → Step2WaitingForMyReview
//          → Step3IssuesAssignedToMe
//            → Summary
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'jTlRRGkplMn4S4aI',
    name: 'GitHub — what needs me',
    active: false,
    isArchived: false,
    settings: { executionOrder: 'v1' },
})
export class GithubWhatNeedsMeWorkflow {
    // =====================================================================
    // CONFIGURATION DES NOEUDS
    // =====================================================================

    @node({
        id: 'a51a9b58-1a1e-4b9d-9d0b-7a6f3e2c8f10',
        name: 'Note',
        type: 'n8n-nodes-base.stickyNote',
        version: 1,
        position: [-560, 60],
    })
    Note = {
        content: `## The hub's GitHub pane
Reads only, three questions: what did I open, what is waiting for my review, what is assigned to me.

Every step is one \`gh\` call through the same seam as the Jira flow (\`n8n/bin/flow-call.sh gh ...\`), so \`gh\`'s own credential — the keyring — is used and nothing has to be pasted into n8n.

Empty is a real answer: "nothing waiting" is what a quiet week looks like.
`,
        height: 300,
        width: 470,
        color: 4,
    };

    @node({
        id: 'b6c1de2a-90f4-4d0e-9b3a-2c5e8f1a7d21',
        name: 'Manual Trigger',
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [-80, 180],
    })
    ManualTrigger = {};

    @node({
        id: 'c7d2ef3b-a105-4e1f-8c4b-3d6f9a2b8e32',
        name: 'Config',
        type: 'n8n-nodes-base.set',
        version: 3.4,
        position: [140, 180],
    })
    Config = {
        assignments: {
            assignments: [
                {
                    id: 'd8e3f04c-b216-4f20-9d5c-4e7fab3c9f43',
                    name: 'repo',
                    value: "={{ $env.GODJIRA_REPO || $env.HOME + '/Projects/godjira' }}",
                    type: 'string',
                },
                {
                    id: 'e9f4015d-c327-4021-8e6d-5f80bc4da054',
                    name: 'account',
                    value: '@me',
                    type: 'string',
                },
                {
                    id: 'f015126e-d438-4132-9f7e-6091cd5eb165',
                    name: 'limit',
                    value: '20',
                    type: 'string',
                },
            ],
        },
        options: {},
    };

    @node({
        id: '0126237f-e549-4243-a08f-71a2de6fc276',
        name: 'Step 1 - my open pull requests',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [360, 180],
    })
    Step1MyOpenPullRequests = {
        command:
            "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh gh search prs --author={{ $('Config').first().json.account }} --state=open --limit {{ $('Config').first().json.limit }} --json number,title,repository,url,isDraft,updatedAt",
        executeOnce: true,
    };

    @node({
        id: '1237348a-f65a-4354-b190-82b3ef70d387',
        name: 'Step 2 - waiting for my review',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [600, 180],
    })
    Step2WaitingForMyReview = {
        command:
            "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh gh search prs --review-requested={{ $('Config').first().json.account }} --state=open --limit {{ $('Config').first().json.limit }} --json number,title,repository,url,updatedAt",
        executeOnce: true,
    };

    @node({
        id: '2348459b-076b-4465-c2a1-93c4f081e498',
        name: 'Step 3 - issues assigned to me',
        type: 'n8n-nodes-base.executeCommand',
        version: 1,
        position: [840, 180],
    })
    Step3IssuesAssignedToMe = {
        command:
            "={{ $('Config').first().json.repo }}/n8n/bin/flow-call.sh gh search issues --assignee={{ $('Config').first().json.account }} --state=open --limit {{ $('Config').first().json.limit }} --json number,title,repository,url,updatedAt",
        executeOnce: true,
    };

    @node({
        id: '3459560c-187c-4576-d3b2-a4d50192f5a9',
        name: 'Summary',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [1080, 180],
    })
    Summary = {
        mode: 'runOnceForAllItems',
        language: 'javaScript',
        jsCode: `const read = (json) => {
  try { return JSON.parse(json.stdout || '{}'); } catch (e) { return { exitCode: null, payload: null, raw: json.stdout || '' }; }
};
const list = (node) => {
  const r = read($(node).first().json);
  if (Array.isArray(r.payload)) return r.payload;          // gh search --json prints an array
  return { error: (r.raw || 'no answer').trim().split('\\n')[0] };   // a step that could not answer
};
const asList = (x) => (Array.isArray(x) ? x : []);
const mine = list('Step 1 - my open pull requests');
const review = list('Step 2 - waiting for my review');
const issues = list('Step 3 - issues assigned to me');

const short = (items) => asList(items).slice(0, 5).map((it) =>
  (it.repository && it.repository.nameWithOwner ? it.repository.nameWithOwner + '#' : '') + it.number + ' ' + (it.title || ''));
const failed = [mine, review, issues].find((x) => !Array.isArray(x));

let note;
if (failed) note = 'github could not answer: ' + failed.error;
else if (!mine.length && !review.length && !issues.length) note = 'nothing waiting on GitHub';
else note = mine.length + ' of mine open, ' + review.length + ' waiting for my review, ' + issues.length + ' assigned to me';

return [{ json: {
  note: note,
  myPullRequests: short(mine),
  waitingForMyReview: short(review),
  assignedToMe: short(issues),
  counts: { mine: asList(mine).length, review: asList(review).length, issues: asList(issues).length },
} }];`,
    };

    // =====================================================================
    // ROUTAGE ET CONNEXIONS
    // =====================================================================

    @links()
    defineRouting() {
        this.ManualTrigger.out(0).to(this.Config.in(0));
        this.Config.out(0).to(this.Step1MyOpenPullRequests.in(0));
        this.Step1MyOpenPullRequests.out(0).to(this.Step2WaitingForMyReview.in(0));
        this.Step2WaitingForMyReview.out(0).to(this.Step3IssuesAssignedToMe.in(0));
        this.Step3IssuesAssignedToMe.out(0).to(this.Summary.in(0));
    }
}
