import QtQuick
import qs.Commons
import qs.Ui

// Plan: the customer's wish goes in here, the issue proposal comes back.
//
// Nothing about a model lives in this panel: the text is handed to whatever agent
// is configured (JIRA_FLOW_AGENT in jira_flow.py -- claude, codex, agy, opencode,
// hermes), and GodJIRA only reads the answer. Nothing is written to Jira until the
// second press, and then exactly the list you just read -- the same two-press
// gesture as "Ta nästa".
Item {
  id: planView
  anchors.fill: parent

  property var app: null

  function t(key, args) { return app ? app.t(key, args) : key }

  property string wish: ""
  property var proposal: []
  property var created: []
  property string error: ""
  property bool busy: false

  function reset() {
    proposal = []
    created = []
    error = ""
  }

  function run(create) {
    if (!app || busy) return
    if (wish.trim() === "") {
      error = t("plan.hint")
      return
    }
    busy = true
    error = ""
    var args = ["plan", "--json", "--text", wish]
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
      if (create) {
        planView.created = parsed.created || []
        planView.proposal = []
        if (app) app.requestSnapshot()
      } else {
        planView.proposal = parsed.proposal || []
        planView.created = []
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
        width: parent.width
        height: 120
        radius: 6
        color: Qt.darker(Color.background, 1.2)
        border.color: Util.alpha(Color.foreground, 0.14)
        border.width: 1

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
