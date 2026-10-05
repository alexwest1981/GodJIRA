import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Ui

// Plan: the customer's wish goes in here, the issue proposal comes back.
//
// Nothing about a model lives in this panel: the text is handed to whatever agent
// is configured (JIRA_FLOW_AGENT in jira_flow.py -- claude, codex, agy, opencode,
// hermes), and GodJIRA only reads the answer. Nothing is written to Jira until the
// second press, and then exactly the list you just read -- the same two-press
// gesture as "Ta nästa".
//
// The wish rarely comes alone: a meeting note, a requirement PDF, a link someone
// pasted in chat. Those go in the same list as the text, and the flow reads them
// (jira_flow.py --context) so the proposal lands on top of the project instead of
// beside it. Three ways in, one list: drop it here, pick it from the disk, or
// paste a link.
Item {
  id: planView
  anchors.fill: parent

  property var app: null

  function t(key, args) { return app ? app.t(key, args) : key }

  property string wish: ""
  property var papers: []
  property var proposal: []
  property var created: []
  property string error: ""
  property string skipped: ""
  property string linkDraft: ""
  property string pdfPath: ""
  property bool busy: false
  property bool picking: false
  property bool dragging: false

  // Pappret skrivs på ett fast ställe i användarens egen state-katalog. Motorn skapar
  // mappen om den inte finns, och filen skrivs om vid varje tolkning.
  readonly property string sheetPath: Quickshell.env("HOME") + "/.local/state/jira-flow/plan.pdf"

  // Vad en post heter i listan: filen, eller länken som den skrevs.
  function paperLabel(item) {
    var s = String(item)
    if (s.indexOf("http://") === 0 || s.indexOf("https://") === 0) return s
    var parts = s.replace(/\/+$/, "").split("/")
    return parts[parts.length - 1] || s
  }

  // En post är en sökväg eller en länk. file:// kommer från släppet i QML och
  // skalas av redan här, så listan visar samma sak som den skickar.
  function addPaper(item) {
    var s = String(item || "").trim().replace(/^file:\/\/(localhost)?/, "")
    if (s === "") return
    var next = papers.slice()
    for (var i = 0; i < next.length; i++) if (String(next[i]) === s) return
    next.push(s)
    papers = next
  }

  function removePaper(index) {
    var next = papers.slice()
    next.splice(index, 1)
    papers = next
  }

  // Släpp: filer och länkar blir underlag, vanlig text blir önskemålet.
  function acceptDrop(drop) {
    dragging = false
    if (drop.hasUrls && drop.urls.length > 0) {
      for (var i = 0; i < drop.urls.length; i++) addPaper(String(drop.urls[i]))
      return
    }
    var text = String(drop.text || "").trim()
    if (/^https?:\/\//i.test(text) || text.charAt(0) === "/" || /^file:\/\//i.test(text)) {
      addPaper(text)
    } else if (text !== "") {
      wish = wish === "" ? text : wish + "\n" + text
    }
  }

  // Filväljaren ligger i flödet, inte i panelen: samma process, samma svar, och
  // en maskin utan dialog säger det i stället för att knappen är tyst.
  function pickPapers() {
    if (!app || picking) return
    picking = true
    error = ""
    app.callFlow(["pick", "--json", "--title", t("plan.addFile")], function(parsed) {
      planView.picking = false
      if (!parsed) {
        planView.error = planView.t("panel.bridgeError")
        return
      }
      if (parsed.ok === false) {
        planView.error = parsed.error || planView.t("panel.bridgeError")
        return
      }
      var chosen = parsed.files || []
      for (var i = 0; i < chosen.length; i++) planView.addPaper(chosen[i])
    })
  }

  function reset() {
    proposal = []
    created = []
    error = ""
    skipped = ""
  }

  // Vad flödet inte kunde läsa (en fil i en släppt mapp, en död länk): det står i
  // svaret, och det hör synas -- annars ser ett halvt underlag ut som ett helt.
  function noteSkips(parsed) {
    var notes = (parsed && parsed.context && parsed.context.documents) || []
    var rows = []
    for (var i = 0; i < notes.length; i++) {
      if (notes[i].error) rows.push(noteName(notes[i].path) + " — " + notes[i].error)
    }
    skipped = rows.join("\n")
  }

  function noteName(path) { return paperLabel(path) }

  function run(create) {
    if (!app || busy) return
    if (linkDraft.trim() !== "") {
      addPaper(linkDraft)
      linkDraft = ""
    }
    if (wish.trim() === "" && papers.length === 0) {
      error = t("plan.hint")
      return
    }
    busy = true
    error = ""
    skipped = ""
    var args = ["plan", "--json"]
    if (wish.trim() !== "") args.push("--text", wish)
    for (var i = 0; i < papers.length; i++) args.push("--context", String(papers[i]))
    // Fördelningen som papper, på ett fast ställe i användarens egen state-katalog:
    // motorn skapar mappen om den inte finns, och knappen nedan öppnar filen.
    if (!create) args.push("--pdf", planView.sheetPath)
    if (create) args.push("--create")
    app.callFlow(args, function(parsed) {
      planView.busy = false
      if (!parsed) {
        planView.error = planView.t("panel.bridgeError")
        return
      }
      if (parsed.ok === false) {
        planView.error = parsed.error || planView.t("panel.bridgeError")
        return
      }
      planView.noteSkips(parsed)
      if (create) {
        planView.created = parsed.created || []
        planView.proposal = []
        // Ett tomt fält efter skrivningen: annars skapar nästa tryck samma ärenden
        // en gång till, och det syns ingenstans förrän på tavlan.
        planView.wish = ""
        planView.papers = []
        if (app) app.requestSnapshot()
      } else {
        planView.proposal = parsed.proposal || []
        planView.created = []
        planView.pdfPath = parsed.pdf || ""
      }
    })
  }

  Flickable {
    anchors.fill: parent
    anchors.margins: Style.space(10)
    clip: true
    contentHeight: column.height
    boundsBehavior: Flickable.StopAtBounds

    Column {
      id: column
      width: parent.width
      spacing: Style.space(8)

      Text {
        width: parent.width
        text: planView.t("plan.heading")
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Rectangle {
        id: wishBox
        width: parent.width
        height: 120
        radius: 6
        color: Qt.darker(Color.background, 1.2)
        border.color: planView.dragging ? Color.accent : Util.alpha(Color.foreground, 0.14)
        border.width: planView.dragging ? 2 : 1

        Flickable {
          anchors.fill: parent
          anchors.margins: 8
          clip: true
          contentHeight: wishEdit.contentHeight
          boundsBehavior: Flickable.StopAtBounds

          TextEdit {
            id: wishEdit
            width: parent.width
            text: planView.wish
            wrapMode: TextEdit.Wrap
            color: Color.foreground
            selectionColor: Color.accent
            selectedTextColor: Color.background
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            onTextChanged: planView.wish = text
          }
        }

        Text {
          anchors.fill: parent
          anchors.margins: 8
          visible: planView.wish === "" && planView.papers.length === 0
          wrapMode: Text.Wrap
          text: planView.t("plan.dropHint")
          color: Util.alpha(Color.foreground, 0.45)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }

        DropArea {
          anchors.fill: parent
          keys: ["text/uri-list", "text/plain"]
          onEntered: planView.dragging = true
          onExited: planView.dragging = false
          onDropped: function(drop) { planView.acceptDrop(drop) }
        }
      }

      RowLayout {
        width: parent.width
        spacing: Style.space(6)

        Button {
          text: planView.t("plan.addFile")
          bordered: true
          fontSize: Style.font.caption
          horizontalPadding: Style.space(8)
          enabled: !planView.busy && !planView.picking
          onClicked: planView.pickPapers()
        }

        // Länken går in på Enter eller vid nästa tryck på Propose — samma lista,
        // samma väg till flödet som en släppt fil.
        TextField {
          id: linkField
          Layout.fillWidth: true
          placeholderText: planView.t("plan.linkPlaceholder")
          text: planView.linkDraft
          onTextChanged: planView.linkDraft = text
          onAccepted: {
            planView.addPaper(planView.linkDraft)
            planView.linkDraft = ""
          }
        }
      }

      // Underlaget, så det syns vad agenten fick -- och går att ta bort igen.
      Repeater {
        model: planView.papers

        Row {
          required property var modelData
          required property int index
          spacing: Style.space(6)

          Text {
            text: "• " + planView.paperLabel(modelData)
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Button {
            text: "✕"
            bordered: true
            fontSize: Style.font.caption
            horizontalPadding: Style.space(4)
            tooltipText: planView.t("panel.close")
            onClicked: planView.removePaper(index)
          }
        }
      }

      Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: planView.t("plan.hint")
        color: Qt.darker(Color.foreground, 1.5)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }

      Row {
        spacing: Style.space(6)

        Button {
          text: planView.t("plan.propose")
          bordered: true
          fontSize: Style.font.caption
          horizontalPadding: Style.space(8)
          enabled: !planView.busy
          onClicked: planView.run(false)
        }

        Button {
          text: planView.t("plan.create")
          bordered: true
          fontSize: Style.font.caption
          horizontalPadding: Style.space(8)
          enabled: !planView.busy && planView.proposal.length > 0
          tooltipText: planView.t("plan.hint")
          onClicked: planView.run(true)
        }

        // Pappret med fördelningen: öppnas i läsaren, inte i panelen. Knappen finns
        // bara när filen blev något -- en knapp till ingenting är värre än ingen knapp.
        Button {
          text: planView.t("plan.pdf")
          bordered: true
          fontSize: Style.font.caption
          horizontalPadding: Style.space(8)
          visible: planView.pdfPath !== ""
          onClicked: Util.execDetached("xdg-open " + Util.shellQuote(planView.pdfPath))
        }
      }

      Text {
        width: parent.width
        wrapMode: Text.Wrap
        visible: planView.error !== ""
        text: planView.error
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }

      Text {
        width: parent.width
        wrapMode: Text.Wrap
        visible: planView.skipped !== ""
        text: planView.skipped
        color: Util.alpha(Color.foreground, 0.6)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }

      Repeater {
        model: planView.proposal

        Text {
          required property var modelData
          required property int index
          width: column.width
          wrapMode: Text.Wrap
          text: (index + 1) + ". [" + (modelData.type || "Task") + "] " + modelData.summary
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }
      }

      Repeater {
        model: planView.created

        Text {
          required property var modelData
          width: column.width
          wrapMode: Text.Wrap
          text: (modelData.key || "") + "  " + (modelData.summary || "")
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }
      }
    }
  }
}
