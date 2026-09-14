import QtQuick
import qs.Commons
import qs.Ui

// Development: what the code side knows about one issue - pull requests,
// branches, commits and builds from Jira's dev-status API - plus the issue's
// own change history, which is always there.
//
// dev-status only answers for git providers the site has connected. When none
// is connected the panel says so instead of showing four empty lists, and the
// history below it still has content.
Item {
  id: devView
  anchors.fill: parent

  property var app: null
  property int rev: app ? app.snapshotRev : -1
  property string selKey: app ? app.selectedIssueKey : ""

  property var issue: app && selKey ? app.issueByKey(selKey) : null
  property var dev: app ? app.devData : null
  property bool loading: app ? app.devLoading : false

  function refresh() {
    if (!app || !selKey) return
    app.loadDev(selKey, true)
  }

  function recentIssues() {
    var board = app ? app.currentBoard() : null
    if (!board) return []
    var all = (board.issues || []).concat(board.backlog || [])
    all.sort(function(a, b) { return (b.updatedMs || 0) - (a.updatedMs || 0) })
    return all.slice(0, 12)
  }

  function sectionRows(field) {
    return (dev && dev[field]) ? dev[field] : []
  }

  function counts() {
    return (dev && dev.counts) ? dev.counts : ({})
  }

  onSelKeyChanged: if (selKey) app.loadDev(selKey, false)
  onRevChanged: if (selKey) app.loadDev(selKey, false)

  Component.onCompleted: if (selKey) app.loadDev(selKey, false)

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
        spacing: Style.space(10)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: devView.issue ? devView.issue.key : "Utveckling"
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          width: parent.width - 340
          elide: Text.ElideRight
          text: devView.issue ? devView.issue.summary : "Välj ett ärende för att se kodstatus och historik."
          color: Qt.darker(Color.foreground, 1.35)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }
      }

      Row {
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(6)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: devView.loading ? "läser…" : ""
          color: Qt.darker(Color.foreground, 1.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        Button {
          text: "Uppdatera"
          fontSize: Style.font.caption
          visible: devView.selKey !== ""
          onClicked: devView.refresh()
        }
        Button {
          text: "Öppna"
          fontSize: Style.font.caption
          visible: devView.selKey !== ""
          tooltipText: "Öppna i webbläsare"
          onClicked: devView.app.openInBrowser(devView.selKey)
        }
      }
    }

    // ---- body
    Item {
      width: parent.width
      height: parent.height - 52

      // no issue picked yet: offer the most recently touched ones
      Column {
        anchors.fill: parent
        anchors.margins: 20
        spacing: Style.space(8)
        visible: devView.selKey === ""

        Text {
          text: "Välj ett ärende"
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          font.bold: true
        }
        Text {
          width: parent.width
          text: "Utvecklingsvyn visar kodstatus och historik för ett ärende. Senast uppdaterade:"
          color: Qt.darker(Color.foreground, 1.45)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap
        }
        Repeater {
          model: devView.recentIssues()

          Rectangle {
            required property var modelData
            width: parent.width
            height: 30
            radius: 6
            color: Qt.darker(Color.background, 1.12)

            Row {
              anchors.fill: parent
              anchors.leftMargin: 10
              anchors.rightMargin: 10
              spacing: Style.space(10)

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.key
                width: 90
                color: Qt.darker(Color.foreground, 1.25)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
              }
              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.summary
                width: parent.width - 90 - 110
                elide: Text.ElideRight
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.statusName || ""
                width: 100
                horizontalAlignment: Text.AlignRight
                color: Qt.darker(Color.foreground, 1.4)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: devView.app.openIssue(modelData.key)
            }
          }
        }
      }

      Flickable {
        anchors.fill: parent
        anchors.margins: 16
        visible: devView.selKey !== ""
        clip: true
        contentHeight: devCol.height
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: devCol
          width: parent.width
          spacing: Style.space(12)

          // integration state
          Rectangle {
            width: parent.width
            height: integrationCol.height + 24
            radius: 10
            color: Qt.darker(Color.background, 1.08)
            border.color: Util.alpha(Color.foreground, 0.08)
            border.width: 1

            Column {
              id: integrationCol
              x: 14
              y: 12
              width: parent.width - 28
              spacing: 6

              Row {
                spacing: Style.space(8)
                Rectangle {
                  width: 8; height: 8; radius: 4
                  anchors.verticalCenter: parent.verticalCenter
                  color: (devView.dev && devView.dev.configured) ? "#4f9d69" : "#c9a227"
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: (devView.dev && devView.dev.configured)
                    ? "Git-integration kopplad"
                    : "Ingen Git-integration kopplad till sajten"
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
              }
              Text {
                width: parent.width
                wrapMode: Text.Wrap
                text: (devView.dev && devView.dev.configured)
                  ? "Jira rapporterar kodstatus för det här ärendet: " + devView.counts()["pullrequest"] + " pull requests, "
                    + devView.counts()["branch"] + " grenar, " + devView.counts()["commit"] + " commits, "
                    + devView.counts()["build"] + " byggen."
                  : "Jira har ingen GitHub/Bitbucket/GitLab-app kopplad för det här projektet, så det finns inga grenar, PR:er eller byggen att visa. Koppla en sådan app i Atlassian Marketplace om du vill ha den här panelen fylld. Historiken nedan kommer från ärendet självt och visas alltid."
                color: Qt.darker(Color.foreground, 1.45)
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          // pull requests
          Rectangle {
            width: parent.width
            height: prCol.height + 24
            visible: devView.sectionRows("pullRequests").length > 0
            radius: 10
            color: Qt.darker(Color.background, 1.08)
            border.color: Util.alpha(Color.foreground, 0.08)
            border.width: 1

            Column {
              id: prCol
              x: 14
              y: 12
              width: parent.width - 28
              spacing: 5

              Text {
                text: "Pull requests"
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.bold: true
              }
              Repeater {
                model: devView.sectionRows("pullRequests")
                Text {
                  required property var modelData
                  width: prCol.width
                  elide: Text.ElideRight
                  text: "#" + modelData.id + "  " + (modelData.status || "") + "  " + modelData.title
                  color: Qt.darker(Color.foreground, 1.25)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }

          // branches / commits / builds
          Rectangle {
            width: parent.width
            height: gitCol.height + 24
            visible: devView.sectionRows("branches").length > 0 || devView.sectionRows("commits").length > 0
                     || devView.sectionRows("builds").length > 0
            radius: 10
            color: Qt.darker(Color.background, 1.08)
            border.color: Util.alpha(Color.foreground, 0.08)
            border.width: 1

            Column {
              id: gitCol
              x: 14
              y: 12
              width: parent.width - 28
              spacing: 5

              Text {
                text: "Grenar, commits och byggen"
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.bold: true
              }
              Repeater {
                model: devView.sectionRows("branches")
                Text {
                  required property var modelData
                  width: gitCol.width
                  elide: Text.ElideRight
                  text: "gren  " + modelData.name + (modelData.lastCommit ? "  ·  " + modelData.lastCommit : "")
                  color: Qt.darker(Color.foreground, 1.25)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
              Repeater {
                model: devView.sectionRows("commits")
                Text {
                  required property var modelData
                  width: gitCol.width
                  elide: Text.ElideRight
                  text: "commit  " + modelData.id + "  " + modelData.message
                  color: Qt.darker(Color.foreground, 1.25)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
              Repeater {
                model: devView.sectionRows("builds")
                Text {
                  required property var modelData
                  width: gitCol.width
                  elide: Text.ElideRight
                  text: "bygge  " + modelData.name + "  " + (modelData.status || "")
                  color: Qt.darker(Color.foreground, 1.25)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }

          // history
          Rectangle {
            width: parent.width
            height: historyCol.height + 24
            radius: 10
            color: Qt.darker(Color.background, 1.08)
            border.color: Util.alpha(Color.foreground, 0.08)
            border.width: 1

            Column {
              id: historyCol
              x: 14
              y: 12
              width: parent.width - 28
              spacing: 5

              Text {
                text: "Historik"
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.bold: true
              }
              Text {
                width: parent.width
                visible: devView.sectionRows("history").length === 0
                text: "Inga ändringar loggade på det här ärendet."
                color: Qt.darker(Color.foreground, 1.55)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Repeater {
                model: devView.sectionRows("history")
                Row {
                  required property var modelData
                  width: historyCol.width
                  spacing: Style.space(8)

                  Text {
                    text: modelData.createdMs
                      ? Qt.formatDateTime(new Date(modelData.createdMs), "d MMM HH:mm")
                      : ""
                    width: 110
                    color: Qt.darker(Color.foreground, 1.5)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                  Text {
                    text: modelData.authorName || ""
                    width: 120
                    elide: Text.ElideRight
                    color: Qt.darker(Color.foreground, 1.35)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                  Text {
                    text: modelData.text || ""
                    width: parent.width - 240
                    elide: Text.ElideRight
                    color: Qt.darker(Color.foreground, 1.2)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }
              }
            }
          }

          Item { width: 1; height: 6 }
        }
      }
    }
  }
}
