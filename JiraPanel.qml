import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Root plugin item for custom.jira.
//
// Omarchy loads this file as a "panel" plugin (kinds: panel + bar-widget,
// keepLoaded: true) through the shell's on-demand loader. The shell calls
// open()/close(), and everything else happens through the IpcHandler below
// (target "custom.jira"), which is what the bar widget drives:
//
//   omarchy-shell shell toggle custom.jira '{}'   <- bar icon
//   omarchy-shell shell call custom.jira refresh  <- bar right-click
//
// The root owns the FloatingWindow (the actual client), every bit of
// application state, and the single Python bridge process that talks to
// Jira (real or mock). Views are thin and receive `app: root`.

Item {
  id: root

  // ------------------------------------------------------------- plugin
  // Injected by omarchy-shell.
  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  property var shell: null
  property var manifest: null

  property bool closingFromHost: false

  function open(payloadJson) {
    root.closingFromHost = false
    if (!root.fittedToScreen) {
      root.fittedToScreen = true
      Qt.callLater(function() { jiraWindow.fitToScreen() })
    }
    jiraWindow.visible = true
    if (!root.booted) {
      root.booted = true
      root.requestStatus()
    } else if (root.connected && !root.snapshot) {
      root.requestSnapshot()
    }
    Qt.callLater(function() {
      if (jiraWindow.visible && connectOverlay.visible) connectSiteInput.forceActiveFocus()
    })
    return "ok"
  }

  function close() {
    root.closingFromHost = true
    jiraWindow.visible = false
    root.closingFromHost = false
    return "ok"
  }

  function requestClose() {
    if (root.shell && typeof root.shell.hide === "function") {
      root.shell.hide("custom.jira")
    } else {
      root.close()
    }
    return "ok"
  }

  function toggle() {
    return jiraWindow.visible ? root.close() : root.open("{}")
  }

  function ping() { return "ok" }

  function refresh() {
    root.requestSnapshot()
    return "ok"
  }

  IpcHandler {
    target: "custom.jira"
    function open(): string { return root.open("{}") }
    function close(): string { return root.close() }
    function toggle(): string { return root.toggle() }
    function refresh(): string { return root.refresh() }
    function ping(): string { return "ok" }
  }

  // ------------------------------------------------------------- config
  readonly property string configDir: Quickshell.env("HOME") + "/.config/omarchy"
  readonly property string bridgePath: configDir + "/plugins/custom.jira/bin/jira_bridge.py"
  readonly property string pythonPath: "python3"

  property bool booted: false
  property bool fittedToScreen: false
  property bool connected: false
  property string mode: "mock"
  property var account: ({})
  property string statusError: ""
  property string notice: ""

  property var snapshot: null
  property bool loading: false
  property double lastUpdatedMs: 0
  property int snapshotRev: 0

  property string selectedBoardId: ""
  property string selectedIssueKey: ""
  property var issueTransitions: []
  property string issueTransitionsFor: ""
  property bool transitionsLoading: false

  property int tabIndex: 1
  property bool showConnect: false
  property int refreshIntervalMs: 30000

  // ------------------------------------------------------------- bridge
  property bool procBusy: false
  property var currentJob: null
  property var procQueue: []
  property bool snapshotQueued: false

  Process {
    id: bridgeProc
    stdout: StdioCollector {
      id: bridgeOut
      waitForEnd: true
      onStreamFinished: {
        try {
          var txt = String(bridgeOut.text)
          var parsed = null
          try { parsed = JSON.parse(txt.trim()) } catch (e) { parsed = null }
          var job = root.currentJob
          root.currentJob = null
          root.procBusy = false
          if (job && job.onDone) {
            try { job.onDone(parsed, txt) } catch (e) {
              console.warn("custom.jira: bridge callback threw", e)
            }
          }
        } catch (e) {
          root.currentJob = null
          root.procBusy = false
          console.warn("custom.jira: stdout handling failed", e)
        }
        root.pumpBridge()
      }
    }
  }

  function callBridge(args, env, onDone) {
    var job = { args: args, env: env || {}, onDone: onDone || function() {} }
    var q = root.procQueue.slice()
    q.push(job)
    root.procQueue = q
    root.pumpBridge()
  }

  function pumpBridge() {
    if (root.procBusy || root.procQueue.length === 0) return
    var q = root.procQueue.slice()
    var job = q.shift()
    root.procQueue = q
    root.currentJob = job
    root.procBusy = true
    var argv = [root.pythonPath, root.bridgePath].concat(job.args)
    bridgeProc.environment = job.env
    bridgeProc.command = argv
    bridgeProc.running = true
  }

  // ------------------------------------------------------------- status
  function requestStatus() {
    root.loading = true
    root.callBridge(["status"], {}, function(parsed) {
      root.loading = false
      if (!parsed) {
        root.statusError = "Bryggan svarade inte korrekt."
        root.showConnect = true
        return
      }
      if (!parsed.ok && parsed.error) {
        root.statusError = parsed.error
      }
      root.mode = parsed.mode || "mock"
      root.connected = !!parsed.connected
      root.account = parsed.account || {}
      if (root.connected) {
        root.showConnect = false
        root.requestSnapshot()
      } else {
        root.showConnect = true
      }
    })
  }

  // ------------------------------------------------------------- snapshot
  function requestSnapshot() {
    if (root.snapshotQueued) return
    if (!root.connected && root.mode !== "mock") return
    root.snapshotQueued = true
    root.loading = true
    root.callBridge(["snapshot"], {}, function(parsed, txt) {
      root.snapshotQueued = false
      root.loading = false
      if (!parsed || !parsed.ok) {
        var msg = (parsed && parsed.error) || "Snapshot misslyckades: " + txt
        root.statusError = msg
        root.notice = ""
        if (!root.connected && root.showConnect) return
        return
      }
      root.statusError = ""
      root.mode = parsed.mode || root.mode
      root.account = parsed.account || root.account
      root.applySnapshot(parsed)
      if (!root.selectedBoardId && parsed.boards && parsed.boards.length > 0) {
        root.selectedBoardId = String(parsed.boards[0].id)
      }
      root.lastUpdatedMs = Date.now()
    })
  }

  function applySnapshot(parsed) {
    root.snapshot = parsed
    root.snapshotRev = root.snapshotRev + 1
    // The detail panel stays open across refreshes. If the issue under it
    // changed status (someone moved it, or we just did), its transition list
    // is stale - refresh it, but only then, so a 30s poll does not hammer
    // the transitions endpoint for an unchanged ticket.
    if (root.selectedIssueKey && root.issueTransitionsFor === root.selectedIssueKey) {
      var issue = root.issueByKey(root.selectedIssueKey)
      if (issue && issue.statusId !== root._transStatusAt) root.refreshTransitions(root.selectedIssueKey)
    }
    root.reconcileSelection()
  }

  property string _transStatusAt: ""

  // ------------------------------------------------------------- helpers
  function boardsList() {
    var out = []
    if (!root.snapshot) return out
    var boards = root.snapshot.boards || []
    for (var i = 0; i < boards.length; i++) {
      out.push({ value: String(boards[i].id), label: boards[i].name })
    }
    return out
  }

  function currentBoard() {
    if (!root.snapshot) return null
    var boards = root.snapshot.boards || []
    for (var i = 0; i < boards.length; i++) {
      if (String(boards[i].id) === root.selectedBoardId) return boards[i]
    }
    return boards.length > 0 ? boards[0] : null
  }

  function issueByKey(key) {
    if (!root.snapshot) return null
    var boards = root.snapshot.boards || []
    for (var b = 0; b < boards.length; b++) {
      var issues = (boards[b].issues || []).concat(boards[b].backlog || [])
      for (var i = 0; i < issues.length; i++) {
        if (issues[i].key === key) return issues[i]
      }
    }
    return null
  }

  function reconcileSelection() {
    if (!root.selectedIssueKey) return
    if (!root.issueByKey(root.selectedIssueKey)) root.selectedIssueKey = ""
  }

  function clearSelectionIfOffBoard() {
    if (!root.selectedIssueKey) return
    var b = root.currentBoard()
    if (!b) return
    var onBoard = false
    var all = (b.issues || []).concat(b.backlog || [])
    for (var i = 0; i < all.length; i++) if (all[i].key === root.selectedIssueKey) { onBoard = true; break }
    if (!onBoard) root.clearIssue()
  }

  function statusColor(issue) {
    if (!issue) return Color.muted
    var cat = issue.statusCategory || "indeterminate"
    if (cat === "done") return "#4f9d69"
    if (cat === "new") return "#c9a227"
    var name = (issue.statusName || "").toLowerCase()
    if (name.indexOf("review") !== -1) return "#8a63c4"
    if (name.indexOf("block") !== -1) return "#c05555"
    return "#4a8fd6"
  }

  function categoryLabel(cat) {
    if (cat === "new") return "Att göra"
    if (cat === "done") return "Klart"
    return "Pågår"
  }

  function initials(name) {
    if (!name) return ""
    var parts = String(name).trim().split(/\s+/)
    var out = ""
    for (var i = 0; i < Math.min(2, parts.length); i++) out += parts[i].charAt(0)
    return out.toUpperCase()
  }

  function ago(ms) {
    if (!ms) return ""
    var s = Math.max(0, Math.floor((Date.now() - ms) / 1000))
    if (s < 5) return "just nu"
    if (s < 60) return s + " s sedan"
    var m = Math.floor(s / 60)
    if (m < 60) return m + " min sedan"
    var h = Math.floor(m / 60)
    if (h < 24) return h + " h sedan"
    return Math.floor(h / 24) + " d sedan"
  }

  function sprintLabel(board) {
    var sp = board && board.sprint
    if (!sp) return ""
    var s = sp.name || "Sprint"
    var start = sp.startMs ? Qt.formatDateTime(new Date(sp.startMs), "d MMM") : ""
    var end = sp.endMs ? Qt.formatDateTime(new Date(sp.endMs), "d MMM") : ""
    if (start && end) return s + "  ·  " + start + " – " + end
    return s
  }

  function myEmail() {
    return (root.account && root.account.email) || ""
  }

  // ------------------------------------------------------------- nav
  property var navModel: [
    { key: "summary", label: "Summary", file: "views/SummaryView.qml" },
    { key: "board", label: "Board", file: "views/BoardView.qml" },
    { key: "backlog", label: "Backlog", file: "views/BacklogView.qml" },
    { key: "dev", label: "Development", file: "views/PlaceholderView.qml" },
    { key: "timeline", label: "Timeline", file: "views/PlaceholderView.qml" },
    { key: "docs", label: "Docs", file: "views/PlaceholderView.qml" }
  ]

  function openIssue(key) {
    if (!key) return
    root.selectedIssueKey = key
    root.refreshTransitions(key)
  }

  function clearIssue() {
    root.selectedIssueKey = ""
    root.issueTransitions = []
    root.issueTransitionsFor = ""
  }

  function openInBrowser(key) {
    var issue = root.issueByKey(key)
    var url = issue && issue.url ? issue.url : ""
    if (url && url !== "https://example.atlassian.net/browse/" + key && root.mode !== "mock") {
      Util.execDetached("xdg-open " + Util.shellQuote(url))
      return
    }
    root.notice = root.mode === "mock"
      ? "Mock-läge: ingen webbläsare att öppna. "
      : ""
  }

  // ------------------------------------------------------------- transitions
  function refreshTransitions(key) {
    if (!key) return
    root.transitionsLoading = true
    root.issueTransitionsFor = key
    var issue = root.issueByKey(key)
    root._transStatusAt = issue ? issue.statusId : ""
    root.callBridge(["transitions", key], {}, function(parsed) {
      root.transitionsLoading = false
      if (parsed && parsed.ok) {
        root.issueTransitions = parsed.transitions || []
      } else {
        root.issueTransitions = []
        root.statusError = (parsed && parsed.error) || "Kunde inte läsa statusar."
      }
    })
  }

  function moveIssue(key, target) {
    root.notice = ""
    root.callBridge(["move", key, target], {}, function(parsed) {
      if (parsed && parsed.ok) {
        root.applySnapshot(parsed)
      } else {
        root.statusError = (parsed && parsed.error) || "Flytten misslyckades."
        root.refreshTransitions(key)
      }
    })
  }

  // ------------------------------------------------------------- create/drop
  function snapshotKeysOf(snap) {
    var out = []
    if (!snap) return out
    var boards = snap.boards || []
    for (var b = 0; b < boards.length; b++) {
      var list = (boards[b].issues || []).concat(boards[b].backlog || [])
      for (var i = 0; i < list.length; i++) out.push(list[i].key)
    }
    return out
  }

  function boardCanAdd() {
    var b = root.currentBoard()
    return b ? (b.canAdd === undefined ? true : !!b.canAdd) : false
  }

  function boardCanDelete() {
    var b = root.currentBoard()
    return b ? (b.canDelete === undefined ? true : !!b.canDelete) : false
  }

  function createIssue(boardId, summary, statusId) {
    var trimmed = String(summary || "").trim()
    root.statusError = ""
    if (!trimmed) {
      root.statusError = "Sammanfattning saknas."
      return
    }
    var before = root.snapshotKeysOf(root.snapshot)
    root.notice = ""
    root.loading = true
    root.callBridge(["create", String(boardId),
      JSON.stringify({ summary: trimmed, statusId: String(statusId || "") })], {}, function(parsed) {
      root.loading = false
      if (parsed && parsed.ok) {
        root.applySnapshot(parsed)
        var after = root.snapshotKeysOf(parsed)
        var added = ""
        for (var i = 0; i < after.length; i++) {
          if (before.indexOf(after[i]) === -1) { added = after[i]; break }
        }
        root.notice = added ? "Skapade " + added + "." : "Skapade ärendet."
        if (added) root.openIssue(added)
      } else {
        root.statusError = (parsed && parsed.error) || "Kunde inte skapa ärendet."
      }
    })
  }

  function deleteIssue(key) {
    root.statusError = ""
    root.notice = ""
    root.callBridge(["delete", key], {}, function(parsed) {
      if (parsed && parsed.ok) {
        if (root.selectedIssueKey === key) {
          root.selectedIssueKey = ""
          root.issueTransitions = []
          root.issueTransitionsFor = ""
        }
        root.applySnapshot(parsed)
        root.notice = "Raderade " + key + "."
      } else {
        root.statusError = (parsed && parsed.error) || "Kunde inte radera ärendet."
      }
    })
  }

  // ------------------------------------------------------------- connect
  function setModeAndLoad(m) {
    root.notice = ""
    root.callBridge(["configure", JSON.stringify({ mode: m })], {}, function(parsed) {
      root.mode = m
      if (m === "mock") {
        root.connected = true
        root.account = (parsed && parsed.account) || root.account
        root.selectedBoardId = ""
        root.selectedIssueKey = ""
        root.showConnect = false
        root.requestSnapshot()
      } else {
        root.showConnect = true
      }
    })
  }

  function connectMock() {
    root.setModeAndLoad("mock")
  }

  function connectReal(siteUrl, email, token) {
    root.notice = "Ansluter…"
    root.callBridge(["configure", JSON.stringify({
      mode: "real",
      siteUrl: siteUrl.trim(),
      email: email.trim()
    })], {}, function(parsedCfg) {
      root.callBridge(["login"], { JIRA_TOKEN: token }, function(parsed) {
        if (parsed && parsed.ok) {
          root.connected = true
          root.account = parsed.account || {}
          root.mode = "real"
          root.notice = ""
          root.showConnect = false
          root.requestSnapshot()
        } else {
          root.notice = ""
          root.statusError = (parsed && parsed.error) || "Inloggningen misslyckades."
          root.showConnect = true
        }
      })
    })
  }

  function logout() {
    root.callBridge(["logout"], {}, function() {
    root.connected = false
    root.account = {}
    root.snapshot = null
    root.snapshotRev = root.snapshotRev + 1
    root.selectedBoardId = ""
      root.selectedIssueKey = ""
      root.issueTransitions = []
      root.mode = "mock"
      root.callBridge(["configure", JSON.stringify({ mode: "mock", siteUrl: "", email: "" })], {}, function() {
        root.showConnect = true
      })
    })
  }

  // ------------------------------------------------------------- chrome
  FloatingWindow {
    id: jiraWindow
    title: "Jira"
    color: Color.background
    implicitWidth: 1200
    implicitHeight: 760
    minimumSize: Qt.size(720, 500)
    visible: false
    onVisibleChanged: {
      if (visible) autoRefreshTimer.restart()
    }

    // The board columns reflow to any width (BoardView), so the window is
    // happy at a modest floating size. On the very first show, shrink the
    // default size if it would not fit the screen it landed on.
    function fitToScreen() {
      var scr = jiraWindow.screen
      if (!scr) return
      if (jiraWindow.width > scr.width)
        jiraWindow.width = Math.max(720, scr.width - 60)
      if (jiraWindow.height > scr.height)
        jiraWindow.height = Math.max(500, scr.height - 80)
    }

    Rectangle {
      anchors.fill: parent
      color: "transparent"

      Column {
        anchors.fill: parent
        spacing: 0

        // ---- titlebar (drag surface)
        Rectangle {
          id: titleBar
          width: parent.width
          height: 40
          color: Color.bar.background

          MouseArea {
            anchors.fill: parent
            onPressed: {
              jiraWindow.startSystemMove()
            }
          }

          Text {
            anchors.left: parent.left
            anchors.leftMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            text: "Jira"
            color: Color.bar.text
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            font.bold: true
          }

          Row {
            anchors.centerIn: parent
            spacing: Style.space(10)
            visible: root.connected || root.mode === "mock"

            Rectangle {
              width: 7; height: 7; radius: 4
              anchors.verticalCenter: parent.verticalCenter
              color: root.connected ? "#4f9d69" : Color.urgent
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: (root.mode === "mock" ? "Mock-läge" : "")
                  + (root.mode === "mock" && root.account.displayName ? " · " : "")
                  + (root.account.displayName || root.account.email || "")
              color: Qt.darker(Color.bar.text, 1.35)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          Row {
            anchors.right: parent.right
            anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(6)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: root.loading ? "läser…" : (root.lastUpdatedMs ? "uppdaterad " + root.ago(root.lastUpdatedMs) : "")
              color: Qt.darker(Color.bar.text, 1.5)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }

            Button {
              text: "Uppdatera"
              fontSize: Style.font.caption
              onClicked: root.requestSnapshot()
            }
            Button {
              text: "—"
              tooltipText: "Minimera"
              onClicked: jiraWindow.minimized = true
            }
            Button {
              text: "x"
              tooltipText: "Stäng"
              onClicked: root.requestClose()
            }
          }
        }

        // ---- status error strip
        Rectangle {
          width: parent.width
          height: root.statusError ? 30 : 0
          visible: root.statusError !== ""
          color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.12)
          clip: true

          Text {
            anchors.left: parent.left
            anchors.leftMargin: 14
            anchors.right: parent.right
            anchors.rightMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            text: root.statusError
            color: Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
          }
        }

        // ---- body: sidebar + content
        Row {
          width: parent.width
          height: parent.height - titleBar.height - (root.statusError ? 30 : 0)

          // sidebar
          Rectangle {
            id: sideBar
            width: 178
            height: parent.height
            color: Qt.darker(Color.background, 1.05)
            border.color: Util.alpha(Color.foreground, 0.06)
            border.width: 1

            Column {
              anchors.fill: parent
              anchors.topMargin: 10
              spacing: 2

              Repeater {
                model: root.navModel

                Rectangle {
                  required property int index
                  required property var modelData

                  id: navRow
                  width: sideBar.width - 12
                  height: 32
                  x: 6
                  radius: 6
                  color: root.tabIndex === index
                    ? Style.selectedFillFor(Color.foreground, Color.accent, Color.urgent)
                    : "transparent"

                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.label
                    color: root.tabIndex === index ? Color.foreground : Qt.darker(Color.foreground, 1.5)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.bold: root.tabIndex === index
                  }

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: root.tabIndex = index
                  }
                }
              }

              Item { width: 10; height: 6 }

              // connection entry
              Rectangle {
                width: sideBar.width - 12
                height: 32
                x: 6
                radius: 6
                color: "transparent"

                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: 12
                  anchors.verticalCenter: parent.verticalCenter
                  text: "Anslutning"
                  color: Qt.darker(Color.foreground, 1.5)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }
                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  onClicked: {
                    root.showConnect = true
                  }
                }
              }
            }
          }

          // content
          Loader {
            id: viewLoader
            width: parent.width - sideBar.width
            height: parent.height
            onLoaded: {
              if (viewLoader.item) viewLoader.item.app = root
            }
            onStatusChanged: {
              if (viewLoader.status === Loader.Error)
                console.warn("custom.jira view failed:", viewLoader.source)
            }
          }
        }
      }
  // ------------------------------------------------------------------
  // Connect overlay: shown before a site is configured, or on demand.
  // ------------------------------------------------------------------
  Rectangle {
    id: connectOverlay
    anchors.fill: parent
    visible: false
    color: Color.background

    property string siteField: ""
    property string emailField: ""
    property string tokenField: ""
    property string connectError: ""

    function show() { visible = true }
    function hide() { visible = false }

    // Swallow stray clicks so the title bar behind cannot start a move while
    // the form is open; it sits below the controls so they still get input.
    MouseArea { anchors.fill: parent }

    Rectangle {
      anchors.centerIn: parent
      width: 460
      color: "transparent"

      Column {
        width: parent.width
        spacing: Style.space(14)

        Text {
          text: "Anslut till Jira"
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Text {
          width: parent.width
          wrapMode: Text.Wrap
          text: "Kör mot det inbyggda mock-API:et för att testa utan konto, eller anslut din Jira Cloud-site med en API-token."
          color: Qt.darker(Color.foreground, 1.35)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Button {
            text: "Mock-läge"
            bordered: true
            onClicked: {
              connectOverlay.connectError = ""
              root.connectMock()
            }
          }
        }

        Rectangle {
          width: parent.width
          height: 1
          color: Util.alpha(Color.foreground, 0.1)
          anchors.margins: Style.space(6)
        }

        TextField {
          id: connectSiteInput
          width: parent.width
          placeholderText: "site.atlassian.net"
          text: connectOverlay.siteField
          onTextChanged: connectOverlay.siteField = text
        }

        TextField {
          width: parent.width
          placeholderText: "din@epost.se"
          text: connectOverlay.emailField
          onTextChanged: connectOverlay.emailField = text
        }

        TextField {
          width: parent.width
          password: true
          placeholderText: "API-token (id.atlassian.com/manage-profile/security/api-tokens)"
          text: connectOverlay.tokenField
          onTextChanged: connectOverlay.tokenField = text
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Button {
            text: "Anslut"
            bordered: true
            onClicked: {
              connectOverlay.connectError = ""
              if (!connectOverlay.siteField || !connectOverlay.emailField || !connectOverlay.tokenField) {
                connectOverlay.connectError = "Fyll i site, e-post och API-token."
                return
              }
              root.connectReal(connectOverlay.siteField, connectOverlay.emailField, connectOverlay.tokenField)
            }
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: connectOverlay.connectError
            color: Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            width: parent.width - 120
            wrapMode: Text.Wrap
          }
        }
      }
    }
  }
    // toast for transient messages/errors (statusError/notice)
    Rectangle {
      id: toast
      z: 120
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 16
      height: 34
      radius: 17
      visible: root.statusError !== "" || root.notice !== ""
      color: root.statusError !== ""
        ? Util.alpha(Color.urgent, 0.92)
        : Util.alpha(Color.foreground, 0.16)
      width: toastText.implicitWidth + 30

      Text {
        id: toastText
        anchors.centerIn: parent
        text: root.statusError !== "" ? root.statusError : root.notice
        color: root.statusError !== "" ? "#ffffff" : Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
    }
  }


  Connections {
    target: root
    function onConnectedChanged() {
      connectOverlay.visible = root.showConnect || !root.connected
    }
    function onShowConnectChanged() {
      connectOverlay.visible = root.showConnect || !root.connected
    }
  }

  // Loader is mounted after open so first paint is cheap; a nav click or the
  // connection change pulls in the view.
  function loadTab(index) {
    if (index >= 0 && index < root.navModel.length) {
      viewLoader.source = Qt.resolvedUrl(root.navModel[index].file)
    }
  }

  onTabIndexChanged: root.loadTab(root.tabIndex)
  onConnectedChanged: if (root.connected) root.loadTab(root.tabIndex)
  Component.onCompleted: {
    viewLoader.source = Qt.resolvedUrl(root.navModel[root.tabIndex].file)
    // Delay so a freshly (re)loaded plugin does not fight the loader.
    Qt.callLater(function() { connectOverlay.visible = root.showConnect || !root.connected })
  }

  // ------------------------------------------------------------- refresh timer
  Timer {
    id: autoRefreshTimer
    interval: root.refreshIntervalMs
    repeat: true
    running: jiraWindow.visible && (root.connected || root.mode === "mock") && !connectOverlay.visible
    triggeredOnStart: false
    onTriggered: {
      if (!root.loading) root.requestSnapshot()
    }
  }

  // ------------------------------------------------------------- toast timer
  onStatusErrorChanged: {
    if (root.statusError !== "") { toastTimer.interval = 12000; toastTimer.restart() }
  }
  onNoticeChanged: {
    if (root.notice !== "") { toastTimer.interval = 6000; toastTimer.restart() }
  }
  Timer {
    id: toastTimer
    onTriggered: {
      root.statusError = ""
      root.notice = ""
    }
  }
}
