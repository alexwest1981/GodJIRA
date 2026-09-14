import QtQuick
import qs.Commons
import qs.Ui

// Detail drawer for a single issue: read it, move it (status and sprint), edit
// it, comment on it, or delete it. State lives on the root (app); this file
// only owns the in-progress form values.
Rectangle {
  id: detail
  property var app: null


  // Texterna kommer från bryggan (samma i18n/*.json som den använder): en källa
  // för varje mening, och språket byts i Inställningar.
  function t(key, args) { return app ? app.t(key, args) : key }

  property int rev: app ? app.snapshotRev : -1
  property string selKey: app ? app.selectedIssueKey : ""
  property var issue: null
  property bool busy: app ? app.transitionsLoading : false
  // Bound (not re-derived in a function) so the priority/assignee rows light
  // up the moment the project's options arrive, which is after the selection.
  property var options: app ? app.projectOptions : ({})
  property var comments: app ? app.issueComments : []

  // edit form state
  property string editSummary: ""
  property string editDescription: ""
  property string editPriority: ""
  property string editAssignee: ""
  property string editPoints: ""
  property bool saving: false
  // comment draft
  property string draft: ""

  anchors.fill: parent

  color: Qt.darker(Color.background, 1.05)
  border.color: Util.alpha(Color.foreground, 0.08)
  border.width: 1

  visible: issue !== null

  function reload() {
    issue = (app && app.selectedIssueKey) ? app.issueByKey(app.selectedIssueKey) : null
    syncForm()
  }

  function syncForm() {
    if (!issue) {
      editSummary = ""; editDescription = ""; editPriority = ""
      editAssignee = ""; editPoints = ""
      return
    }
    editSummary = issue.summary || ""
    editDescription = issue.description || ""
    editPriority = issue.priorityName || ""
    editAssignee = issue.assigneeName || ""
    editPoints = pointsText(issue)
    draft = ""
  }

  function pointsText(iss) {
    if (!iss || iss.storyPoints === null || iss.storyPoints === undefined || iss.storyPoints === "") return ""
    return String(iss.storyPoints)
  }

  function chipVisible(value) {
    return value !== undefined && value !== null && String(value) !== ""
  }

  function canMove() {
    return detail.app && detail.app.issueTransitions.length > 0
  }

  function statusText() {
    if (detail.busy) return t("detail.loadingTransitions")
    if (!detail.app) return ""
    return detail.app.issueTransitions.length === 0
      ? t("detail.noTransitions")
      : ""
  }

  function sprints() {
    return detail.app ? detail.app.boardSprints() : []
  }

  function people() {
    return options.people || []
  }

  function priorities() {
    return options.priorities || []
  }

  function currentSprintId() {
    return detail.issue ? String(detail.issue.sprintId || "") : ""
  }

  function dirty() {
    if (!detail.issue) return false
    return detail.editSummary !== (detail.issue.summary || "")
      || detail.editDescription !== (detail.issue.description || "")
      || detail.editPriority !== (detail.issue.priorityName || "")
      || detail.editAssignee !== (detail.issue.assigneeName || "")
      || detail.editPoints !== pointsText(detail.issue)
  }

  function save() {
    if (!detail.issue || !detail.app) return
    var payload = {}
    if (detail.editSummary !== (detail.issue.summary || "")) payload.summary = detail.editSummary
    if (detail.editDescription !== (detail.issue.description || "")) payload.description = detail.editDescription
    if (detail.editPriority !== (detail.issue.priorityName || "")) payload.priorityName = detail.editPriority
    if (detail.editPoints !== pointsText(detail.issue)) payload.storyPoints = detail.editPoints
    if (detail.editAssignee !== (detail.issue.assigneeName || "")) {
      var list = detail.people()
      var found = false
      for (var i = 0; i < list.length; i++) {
        if (list[i].displayName === detail.editAssignee) {
          payload.assigneeAccountId = list[i].accountId
          found = true
          break
        }
      }
      if (!found && detail.editAssignee === "") payload.assigneeAccountId = ""
    }
    if (Object.keys(payload).length === 0) {
      detail.app.notice = t("detail.nothingChanged")
      return
    }
    detail.saving = true
    detail.app.saveIssue(detail.issue.key, payload, function(ok) {
      detail.saving = false
      if (ok) detail.syncForm()
    })
  }

  onRevChanged: reload()
  onSelKeyChanged: reload()
  Component.onCompleted: reload()
  onIssueChanged: detail.deleteArmed = false

  Flickable {
    anchors.fill: parent
    clip: true
    contentHeight: detailCol.height + 24
    boundsBehavior: Flickable.StopAtBounds

    Column {
      id: detailCol
      x: 12
      y: 14
      width: parent.width - 24
      spacing: 0

      // ---- header
      Row {
        width: parent.width
        spacing: Style.space(8)

        Text {
          text: detail.issue ? detail.issue.key : ""
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Rectangle {
          width: 8; height: 8; radius: 4
          anchors.verticalCenter: parent.verticalCenter
          color: detail.app && detail.issue ? app.statusColor(detail.issue) : "transparent"
          visible: detail.issue !== null
        }

        Item { width: Math.max(0, parent.width - 230); height: 1 }

        Button {
          text: t("common.open")
          fontSize: Style.font.caption
          tooltipText: t("common.openInBrowser")
          onClicked: app.openInBrowser(detail.issue.key)
        }
        Button {
          text: "x"
          tooltipText: t("detail.close")
          fontSize: Style.font.caption
          onClicked: app.clearIssue()
        }
      }

      Text {
        width: parent.width
        text: detail.issue ? detail.issue.summary : ""
        wrapMode: Text.Wrap
        color: Qt.darker(Color.foreground, 1.15)
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }

      // ---- meta chips
      Flow {
        width: parent.width
        spacing: Style.space(6)

        Rectangle {
          height: 22
          radius: 5
          color: Util.alpha(Color.foreground, 0.08)
          width: chip0.implicitWidth + 14
          visible: detail.chipVisible(detail.issue ? detail.issue.statusName : "")
          Text {
            id: chip0
            anchors.centerIn: parent
            text: detail.issue ? (detail.issue.statusName || "") : ""
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }
        }
        Rectangle {
          height: 22
          radius: 5
          color: Util.alpha(Color.foreground, 0.08)
          width: chip1.implicitWidth + 14
          visible: detail.chipVisible(detail.issue ? (detail.issue.typeName || detail.issue.type) : "")
          Text {
            id: chip1
            anchors.centerIn: parent
            text: detail.issue ? (detail.issue.typeName || detail.issue.type || "") : ""
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
        Rectangle {
          height: 22
          radius: 5
          color: Util.alpha(Color.foreground, 0.08)
          width: chip2.implicitWidth + 14
          visible: detail.chipVisible(detail.issue ? detail.issue.priorityName : "")
          Text {
            id: chip2
            anchors.centerIn: parent
            text: detail.issue ? (detail.issue.priorityName || "") : ""
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
        Rectangle {
          height: 22
          radius: 5
          color: Util.alpha(Color.foreground, 0.08)
          width: chip3.implicitWidth + 14
          visible: detail.issue !== null
          Text {
            id: chip3
            anchors.centerIn: parent
            text: detail.issue ? (detail.issue.assigneeName || t("common.unassigned")) : ""
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
        Rectangle {
          height: 22
          radius: 5
          color: Util.alpha(Color.foreground, 0.08)
          width: chip4.implicitWidth + 14
          visible: detail.chipVisible(detail.issue ? detail.issue.projectKey : "")
          Text {
            id: chip4
            anchors.centerIn: parent
            text: detail.issue ? (detail.issue.projectKey || "") : ""
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
        Rectangle {
          height: 22
          radius: 5
          color: Util.alpha(Color.foreground, 0.08)
          width: chip5.implicitWidth + 14
          visible: detail.chipVisible(detail.issue ? detail.issue.sprintName : "")
          Text {
            id: chip5
            anchors.centerIn: parent
            text: detail.issue ? (detail.issue.sprintName || "") : ""
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
        Rectangle {
          height: 22
          radius: 5
          color: Util.alpha(Color.accent, 0.16)
          width: chip6.implicitWidth + 14
          visible: detail.chipVisible(detail.issue ? detail.issue.parentKey : "")
          Text {
            id: chip6
            anchors.centerIn: parent
            text: detail.issue && detail.issue.parentKey ? t("detail.partOf", { key: detail.issue.parentKey }) : ""
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            enabled: detail.app && detail.app.issueByKey(detail.issue ? detail.issue.parentKey : "") !== null
            onClicked: detail.app.openIssue(detail.issue.parentKey)
          }
        }
      }

      Item { width: 1; height: 10 }

      // ---- description
      Text {
        width: parent.width
        text: t("detail.description")
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }
      Text {
        width: parent.width
        text: detail.issue && (detail.issue.description || "").trim()
          ? detail.issue.description
          : t("detail.noDescription")
        wrapMode: Text.Wrap
        color: detail.issue && (detail.issue.description || "").trim()
          ? Qt.darker(Color.foreground, 1.25)
          : Qt.darker(Color.foreground, 1.6)
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
        maximumLineCount: 12
      }

      Rectangle {
        width: parent.width
        height: 1
        color: Util.alpha(Color.foreground, 0.1)
      }
      Item { width: 1; height: 10 }

      // ---- sprint
      Text {
        width: parent.width
        text: "Sprint"
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }
      Text {
        width: parent.width
        visible: detail.sprints().length === 0
        text: t("detail.noSprints")
        color: Qt.darker(Color.foreground, 1.45)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
      Flow {
        id: sprintFlow
        width: parent.width
        spacing: Style.space(6)
        visible: detail.sprints().length > 0

        Repeater {
          model: detail.sprints()

          Button {
            required property var modelData
            text: modelData.name
            fontSize: Style.font.caption
            selected: String(modelData.id) === detail.currentSprintId()
            tooltipText: detail.app ? detail.app.sprintRange(modelData) : ""
            onClicked: detail.app.assignIssue(detail.issue.key, modelData.id)
          }
        }
        Button {
          text: t("nav.backlog")
          fontSize: Style.font.caption
          selected: detail.currentSprintId() === ""
          tooltipText: t("detail.removeFromSprint")
          onClicked: detail.app.assignIssue(detail.issue.key, "backlog")
        }
      }

      Rectangle {
        width: parent.width
        height: 1
        color: Util.alpha(Color.foreground, 0.1)
      }
      Item { width: 1; height: 10 }

      // ---- edit
      Text {
        width: parent.width
        text: t("detail.edit")
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }

      TextField {
        width: parent.width
        placeholderText: t("board.fieldSummary")
        text: detail.editSummary
        onTextChanged: detail.editSummary = text
      }

      Item { width: 1; height: 6 }

      // multi-line description (a TextInput-based TextField only does one line)
      Rectangle {
        width: parent.width
        height: 92
        radius: 6
        color: Qt.darker(Color.background, 1.2)
        border.color: Util.alpha(Color.foreground, 0.14)
        border.width: 1

        Flickable {
          anchors.fill: parent
          anchors.margins: 8
          clip: true
          contentHeight: descEdit.contentHeight
          boundsBehavior: Flickable.StopAtBounds

          TextEdit {
            id: descEdit
            width: parent.width
            text: detail.editDescription
            wrapMode: TextEdit.Wrap
            color: Color.foreground
            selectionColor: Color.accent
            selectedTextColor: Color.background
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            onTextChanged: detail.editDescription = text
          }
        }
      }

      Item { width: 1; height: 8 }

      Text {
        width: parent.width
        text: t("detail.priority")
        color: Qt.darker(Color.foreground, 1.35)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        visible: detail.priorities().length > 0
      }
      Flow {
        width: parent.width
        spacing: Style.space(4)
        visible: detail.priorities().length > 0

        Repeater {
          model: detail.priorities()
          Button {
            required property var modelData
            text: modelData
            fontSize: Style.font.caption
            selected: detail.editPriority === modelData
            horizontalPadding: Style.space(8)
            onClicked: detail.editPriority = modelData
          }
        }
      }

      Item { width: 1; height: 8 }

      Text {
        width: parent.width
        text: t("detail.assignee")
        color: Qt.darker(Color.foreground, 1.35)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        visible: detail.people().length > 0
      }
      Flow {
        width: parent.width
        spacing: Style.space(4)
        visible: detail.people().length > 0

        Repeater {
          model: detail.people()
          Button {
            required property var modelData
            text: detail.app.initials(modelData.displayName) || modelData.displayName
            fontSize: Style.font.caption
            selected: detail.editAssignee === modelData.displayName
            tooltipText: modelData.displayName + (modelData.email ? "  ·  " + modelData.email : "")
            horizontalPadding: Style.space(8)
            onClicked: detail.editAssignee = modelData.displayName
          }
        }
        Button {
          text: t("common.none")
          fontSize: Style.font.caption
          selected: detail.editAssignee === ""
          horizontalPadding: Style.space(8)
          tooltipText: t("detail.unassign")
          onClicked: detail.editAssignee = ""
        }
      }

      Item { width: 1; height: 8 }

      Row {
        width: parent.width
        spacing: Style.space(8)

        TextField {
          width: 110
          placeholderText: t("detail.points")
          text: detail.editPoints
          onTextChanged: detail.editPoints = text
        }
        Button {
          text: detail.saving ? "Sparar…" : t("detail.saveChanges")
          fontSize: Style.font.caption
          selected: detail.dirty()
          onClicked: detail.save()
        }
        Button {
          text: t("common.reset")
          fontSize: Style.font.caption
          visible: detail.dirty()
          onClicked: detail.syncForm()
        }
      }

      Rectangle {
        width: parent.width
        height: 1
        color: Util.alpha(Color.foreground, 0.1)
      }
      Item { width: 1; height: 10 }

      // ---- comments
      Text {
        width: parent.width
        text: t("detail.comments") + (detail.app ? " (" + detail.app.issueComments.length + ")" : "")
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }
      Text {
        width: parent.width
        visible: detail.app !== null && detail.app.commentsLoading
        text: t("detail.loadingComments")
        color: Qt.darker(Color.foreground, 1.5)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
      Column {
        width: parent.width
        spacing: Style.space(6)

        Repeater {
          model: detail.comments

          Rectangle {
            required property var modelData
            width: parent.width
            height: commentBody.height + 30
            radius: 6
            color: Qt.darker(Color.background, 1.15)

            Column {
              id: commentBody
              x: 10
              y: 8
              width: parent.width - 20
              spacing: 2

              Row {
                width: parent.width
                spacing: Style.space(6)
                Text {
                  text: modelData.authorName || t("common.unknown")
                  color: Qt.darker(Color.foreground, 1.2)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                Text {
                  text: modelData.createdMs
                    ? Qt.formatDateTime(new Date(modelData.createdMs), "d MMM HH:mm")
                    : ""
                  color: Qt.darker(Color.foreground, 1.6)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
              Text {
                width: parent.width
                text: modelData.body || ""
                wrapMode: Text.Wrap
                color: Qt.darker(Color.foreground, 1.25)
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
            }
          }
        }

        Text {
          width: parent.width
          visible: !(detail.app && detail.app.commentsLoading)
                   && (!detail.app || detail.app.issueComments.length === 0)
          text: t("detail.noComments")
          color: Qt.darker(Color.foreground, 1.6)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Item { width: 1; height: 8 }

      Row {
        width: parent.width
        spacing: Style.space(6)

        TextField {
          id: commentField
          width: parent.width - 90
          placeholderText: t("detail.commentPlaceholder")
          text: detail.draft
          onTextChanged: detail.draft = text
          Keys.onReturnPressed: {
            detail.app.addComment(detail.issue.key, detail.draft)
            detail.draft = ""
          }
        }
        Button {
          text: t("detail.send")
          fontSize: Style.font.caption
          width: 80
          onClicked: {
            detail.app.addComment(detail.issue.key, detail.draft)
            detail.draft = ""
          }
        }
      }

      Rectangle {
        width: parent.width
        height: 1
        color: Util.alpha(Color.foreground, 0.1)
      }
      Item { width: 1; height: 10 }

      // ---- transitions
      Text {
        width: parent.width
        text: t("detail.moveIssue")
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }
      Text {
        width: parent.width
        text: detail.statusText()
        color: Qt.darker(Color.foreground, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }

      Flow {
        id: transFlow
        width: parent.width
        spacing: Style.space(6)
        visible: !busy && detail.canMove()

        Repeater {
          model: detail.app ? detail.app.issueTransitions : []
          delegate: Rectangle {
            required property var modelData
            width: transFlow.width - 2
            height: 28
            radius: 6
            color: Qt.darker(Color.background, 1.35)
            border.color: Util.alpha(Color.foreground, 0.12)
            border.width: 1

            Text {
              anchors.left: parent.left
              anchors.leftMargin: 10
              anchors.verticalCenter: parent.verticalCenter
              text: modelData.toStatusName || modelData.name
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Text {
              anchors.right: parent.right
              anchors.rightMargin: 10
              anchors.verticalCenter: parent.verticalCenter
              text: "→"
              color: Qt.darker(Color.foreground, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: app.moveIssue(detail.issue.key, modelData.toStatusName || modelData.toStatusId)
            }
          }
        }
      }

      Item { width: 1; height: 10 }

      Rectangle {
        width: parent.width
        height: 1
        color: Util.alpha(Color.foreground, 0.1)
      }
      Item { width: 1; height: 8 }

      // ---- delete
      //
      // Två steg med flit, och det andra steget ser **annorlunda ut** än det första:
      // en fråga som namnger ärendet, med egna knappar. Tidigare bytte samma knapp
      // bara text, vilket gjorde två snabba klick till en radering utan att man såg
      // vad man gjorde. Går inte att ångra i Jira — därför står det.
      Column {
        width: parent.width
        spacing: 6
        visible: detail.app !== null && detail.app.boardCanDelete() && detail.issue !== null

        Button {
          id: deleteBtn
          width: 160
          visible: !detail.deleteArmed
          text: t("detail.delete")
          fontSize: Style.font.caption
          onClicked: {
            detail.deleteArmed = true
            detail.deleteResetTimer.restart()
          }
        }

        Rectangle {
          visible: detail.deleteArmed
          width: parent.width
          height: confirmCol.implicitHeight + 16
          color: Util.alpha("#c0392b", 0.12)
          border.color: Util.alpha("#c0392b", 0.55)
          border.width: 1

          Column {
            id: confirmCol
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: 8
            spacing: 6

            Text {
              width: parent.width
              text: t("detail.deleteWithKey", { key: detail.issue ? detail.issue.key : "" }) + " — \"" +
                    (detail.issue ? detail.issue.summary : "") + "\"?\n" +
                    t("detail.deleteWarning")
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Row {
              spacing: 8

              Button {
                text: t("conn.cancel")
                fontSize: Style.font.caption
                onClicked: {
                  detail.deleteResetTimer.stop()
                  detail.deleteArmed = false
                }
              }

              Button {
                text: "Radera"
                fontSize: Style.font.caption
                onClicked: {
                  detail.deleteResetTimer.stop()
                  detail.deleteArmed = false
                  detail.app.deleteIssue(detail.issue.key)
                }
              }
            }
          }
        }
      }
    }
  }

  property bool deleteArmed: false
  property Timer deleteResetTimer: Timer {
    interval: 4000
    onTriggered: detail.deleteArmed = false
  }
}
