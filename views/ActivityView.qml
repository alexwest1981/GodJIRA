import QtQuick
import qs.Commons
import qs.Ui

// Activity: the project's most recently changed issues, newest first, grouped
// by day. The badge on each row is what Jira's changelog says changed last
// ("status: To Do -> In Review"), when the site keeps that history.
Item {
  id: activityView
  anchors.fill: parent

  property var app: null

  // Texterna kommer från bryggan (samma i18n/*.json som den använder): en källa
  // för varje mening, och språket byts i Inställningar.
  function t(key, args) { return app ? app.t(key, args) : key }

  property int rev: app ? app.snapshotRev : -1
  property string selKey: app ? app.selectedIssueKey : ""
  property string boardTitle: ""
  property string projectKey: ""

  property var groups: []
  property var rows: app ? app.activityRows : []
  property bool loading: app ? app.activityLoading : false

  property int limit: 40

  function dayLabel(ms) {
    if (!ms) return t("activity.unknownDate")
    var d = new Date(ms)
    var today = new Date()
    var startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
    if (ms >= startOfToday) return "Idag"
    if (ms >= startOfToday - 86400000) return t("activity.yesterday")
    return Qt.formatDateTime(d, "d MMM")
  }

  function rebuild() {
    if (!app) { groups = []; return }
    var board = app.currentBoard()
    if (!board) { groups = []; boardTitle = ""; projectKey = ""; return }
    projectKey = board.projectKey || ""
    boardTitle = projectKey + " · " + board.name
    var src = (app.activityRows || []).slice()
    src.sort(function(a, b) { return (b.updatedMs || 0) - (a.updatedMs || 0) })
    var out = []
    var current = null
    for (var i = 0; i < src.length; i++) {
      var label = dayLabel(src[i].updatedMs)
      if (!current || current.label !== label) {
        current = { label: label, rows: [] }
        out.push(current)
      }
      current.rows.push(src[i])
    }
    groups = out
  }

  function reload() {
    if (!app || !projectKey) return
    app.loadActivity(projectKey, limit, true)
  }

  onProjectKeyChanged: if (projectKey) app.loadActivity(projectKey, limit, false)
  onRowsChanged: rebuild()
  onRevChanged: {
    if (projectKey) app.loadActivity(projectKey, limit, false)
    rebuild()
  }
  Component.onCompleted: rebuild()

  Column {
    anchors.fill: parent
    spacing: 0

    // ---- header
    Rectangle {
      width: parent.width
      height: 52
      color: "transparent"

      Row {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(12)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: activityView.boardTitle
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: t("nav.activity")
          color: Qt.darker(Color.foreground, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Row {
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(8)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: activityView.loading
            ? t("panel.loading")
            : (activityView.app && activityView.app.activityRows ? activityView.t("activity.recentChanges", { count: activityView.app.activityRows.length }) : "")
          color: Qt.darker(Color.foreground, 1.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        Button {
          text: t("panel.refresh")
          fontSize: Style.font.caption
          onClicked: activityView.reload()
        }
      }
    }

    // ---- body
    Item {
      width: parent.width
      height: parent.height - 52

      Row {
        anchors.fill: parent
        spacing: 0

        Rectangle {
          id: listArea
          width: parent.width - (rhsBack.visible ? rhsBack.width : 0)
          height: parent.height
          clip: true
          color: "transparent"

          Text {
            anchors.centerIn: parent
            width: parent.width - 60
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: activityView.app && activityView.app.activityRows && activityView.app.activityRows.length === 0
              ? t("activity.empty")
              : t("activity.loading")
            color: Qt.darker(Color.foreground, 1.5)
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            visible: activityView.groups.length === 0
          }

          ListView {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.topMargin: 6
            anchors.bottomMargin: 8
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: activityView.groups
            spacing: 4

            delegate: Column {
              id: dayCol
              required property var modelData
              width: ListView.view.width
              spacing: 4

              Text {
                topPadding: 8
                text: modelData.label
                color: Qt.darker(Color.foreground, 1.45)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              Repeater {
                model: modelData.rows

                Rectangle {
                  required property var modelData
                  // Not ListView.view: inside the day's Repeater that attached
                  // property is null (the row belongs to the day column).
                  width: dayCol.width
                  height: 48
                  radius: 8
                  color: Qt.darker(Color.background, 1.18)
                  border.color: Util.alpha(Color.foreground, 0.06)
                  border.width: 1

                  Row {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    spacing: Style.space(10)

                    Rectangle {
                      width: 8; height: 8; radius: 4
                      anchors.verticalCenter: parent.verticalCenter
                      color: activityView.app.statusColor(modelData)
                    }
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.key
                      width: 84
                      color: Qt.darker(Color.foreground, 1.25)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                    Column {
                      anchors.verticalCenter: parent.verticalCenter
                      width: parent.width - 84 - 110 - 190 - Style.space(40)
                      spacing: 1

                      Text {
                        width: parent.width
                        text: modelData.summary
                        color: Color.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                        elide: Text.ElideRight
                      }
                      Text {
                        width: parent.width
                        text: modelData.lastChange || modelData.statusName || ""
                        color: Qt.darker(Color.foreground, 1.5)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                      }
                    }
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.assigneeName || t("common.unassigned")
                      width: 110
                      elide: Text.ElideRight
                      color: Qt.darker(Color.foreground, 1.4)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.statusName || ""
                      width: 90
                      elide: Text.ElideRight
                      color: Qt.darker(Color.foreground, 1.35)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      text: activityView.app.ago(modelData.updatedMs)
                      width: 76
                      horizontalAlignment: Text.AlignRight
                      color: Qt.darker(Color.foreground, 1.5)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                  }

                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: activityView.app.openIssue(modelData.key)
                  }
                }
              }
            }
          }
        }

        Rectangle {
          id: rhsBack
          width: 364
          color: "transparent"
          clip: true
          visible: activityView.selKey !== ""

          Loader {
            anchors.fill: parent
            source: "../components/IssueDetail.qml"
            onLoaded: {
              item.app = activityView.app
            }
          }
        }
      }
    }
  }
}
