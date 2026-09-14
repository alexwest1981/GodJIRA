import QtQuick
import qs.Commons
import qs.Ui

// Reports: the burndown and the sprint report for one sprint, plus velocity
// once sprints have closed. Everything comes from the bridge's `report`
// command, which reads Jira's own chart and sprint-report endpoints.
Item {
  id: reportsView
  anchors.fill: parent

  property var app: null

  // Texterna kommer från bryggan (samma i18n/*.json som den använder): en källa
  // för varje mening, och språket byts i Inställningar.
  function t(key, args) { return app ? app.t(key, args) : key }

  property int rev: app ? app.snapshotRev : -1
  property string boardId: ""
  property string boardTitle: ""
  property string sprintId: ""

  property var sprints: []
  property var report: app ? app.reportData : null
  property bool loading: app ? app.reportLoading : false

  function rebuild() {
    var board = app ? app.currentBoard() : null
    if (!board) { sprints = []; boardTitle = ""; sprintId = ""; return }
    boardId = String(board.id)
    boardTitle = (board.projectKey || "") + " · " + board.name
    var list = (board.sprints || []).slice()
    list.sort(function(a, b) {
      var rankA = a.state === "active" ? 0 : (a.state === "future" ? 1 : 2)
      var rankB = b.state === "active" ? 0 : (b.state === "future" ? 1 : 2)
      if (rankA !== rankB) return rankA - rankB
      return (b.startMs || 0) - (a.startMs || 0)
    })
    sprints = list
    var known = false
    for (var i = 0; i < list.length; i++) if (String(list[i].id) === sprintId) known = true
    if (!known) sprintId = list.length > 0 ? String(list[0].id) : ""
    load()
  }

  function load() {
    if (!app || !boardId || !sprintId) return
    app.loadReport(boardId, sprintId, false)
  }

  function selectSprint(id) {
    sprintId = String(id)
    load()
  }

  function points() {
    return (report && report.points) ? report.points : []
  }

  function summary() {
    return (report && report.summary) ? report.summary : ({})
  }

  function maxRemaining() {
    var pts = points()
    var max = 1
    for (var i = 0; i < pts.length; i++) {
      if (pts[i].remaining > max) max = pts[i].remaining
      if (pts[i].total > max) max = pts[i].total
    }
    return max
  }

  // The issue list in the sprint report only has keys; this fills in the
  // summary when the issue is on the board we already hold.
  function summaryOf(key) {
    var issue = app ? app.issueByKey(key) : null
    return issue ? (issue.summary || "") : ""
  }

  function statCards() {
    var s = summary()
    return [
      { label: t("reports.completed"), value: s.completed !== undefined ? s.completed : "–", color: "#4f9d69" },
      { label: t("reports.remaining"), value: s.notCompleted !== undefined ? s.notCompleted : "–", color: "#4a8fd6" },
      { label: t("reports.added"), value: s.added !== undefined ? s.added : "–", color: "#c9a227" },
      { label: t("reports.removed"), value: s.punted !== undefined ? s.punted : "–", color: "#c05555" }
    ]
  }

  Component.onCompleted: rebuild()
  onRevChanged: {
    rebuild()
    chart.requestPaint()
  }
  onSprintIdChanged: chart.requestPaint()
  // The report arrives from the bridge after the first paint; without this the
  // canvas stays on the empty axes it drew while the data was in flight.
  onReportChanged: chart.requestPaint()
  onLoadingChanged: chart.requestPaint()

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
          text: reportsView.boardTitle
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: t("nav.reports")
          color: Qt.darker(Color.foreground, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Row {
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(6)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: reportsView.loading ? t("panel.loading") : ""
          color: Qt.darker(Color.foreground, 1.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        Button {
          text: t("panel.refresh")
          fontSize: Style.font.caption
          onClicked: reportsView.app.loadReport(reportsView.boardId, reportsView.sprintId, true)
        }
      }
    }

    // ---- sprint picker
    Rectangle {
      width: parent.width
      height: reportsView.sprints.length > 0 ? 40 : 0
      visible: reportsView.sprints.length > 0
      color: "transparent"

      Row {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(6)

        Repeater {
          model: reportsView.sprints

          Button {
            required property var modelData

            text: modelData.name
            fontSize: Style.font.caption
            selected: String(modelData.id) === reportsView.sprintId
            tooltipText: reportsView.app.sprintRange(modelData) + " · " + modelData.state
            onClicked: reportsView.selectSprint(modelData.id)
          }
        }
      }
    }

    // ---- body
    Item {
      width: parent.width
      height: parent.height - 52 - (reportsView.sprints.length > 0 ? 40 : 0)

      Text {
        anchors.centerIn: parent
        width: parent.width - 80
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: t("reports.noSprints")
        color: Qt.darker(Color.foreground, 1.5)
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        visible: reportsView.sprints.length === 0
      }

      Flickable {
        anchors.fill: parent
        visible: reportsView.sprints.length > 0
        clip: true
        contentHeight: reportCol.height
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: reportCol
          width: parent.width - 32
          x: 16
          spacing: Style.space(14)

          Item { width: 1; height: 2 }

          // ---- stat cards
          Row {
            width: parent.width
            spacing: Style.space(10)

            Repeater {
              model: reportsView.statCards()

              Rectangle {
                required property var modelData

                width: (reportCol.width - Style.space(10) * 3) / 4
                height: 74
                radius: 10
                color: Qt.darker(Color.background, 1.1)
                border.color: Util.alpha(Color.foreground, 0.08)
                border.width: 1

                Rectangle {
                  width: 3
                  height: parent.height - 20
                  anchors.left: parent.left
                  anchors.leftMargin: 10
                  anchors.verticalCenter: parent.verticalCenter
                  radius: 2
                  color: modelData.color
                }
                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: 22
                  anchors.verticalCenter: parent.verticalCenter
                  width: parent.width - 90
                  wrapMode: Text.Wrap
                  text: modelData.label
                  color: Qt.darker(Color.foreground, 1.3)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
                Text {
                  anchors.right: parent.right
                  anchors.rightMargin: 16
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.value
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.display
                  font.bold: true
                }
              }
            }
          }

          // ---- burndown
          Rectangle {
            width: parent.width
            height: 300
            radius: 10
            color: Qt.darker(Color.background, 1.06)
            border.color: Util.alpha(Color.foreground, 0.08)
            border.width: 1

            Column {
              anchors.fill: parent
              anchors.margins: 14
              spacing: 6

              Text {
                text: "Burndown · " + (reportsView.summary().name || reportsView.app.sprintNameOf(reportsView.sprintId))
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.bold: true
              }
              Text {
                text: reportsView.points().length > 1
                  ? t("reports.burndownHint")
                  : ""
                color: Qt.darker(Color.foreground, 1.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }

              Canvas {
                id: chart
                width: parent.width
                height: 216
                visible: reportsView.points().length > 1

                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()

                onPaint: {
                  var ctx = getContext("2d")
                  ctx.clearRect(0, 0, width, height)

                  var pts = reportsView.points()
                  var s = reportsView.summary()
                  var start = s.startMs || (pts.length > 0 ? pts[0].atMs : 0)
                  var end = s.endMs || (pts.length > 0 ? pts[pts.length - 1].atMs : 0)
                  if (end <= start) end = start + 1

                  var padL = 34, padR = 12, padT = 10, padB = 24
                  var w = width - padL - padR
                  var h = height - padT - padB
                  var maxY = reportsView.maxRemaining()

                  function xOf(ms) { return padL + w * (ms - start) / (end - start) }
                  function yOf(v) { return padT + h - h * (v / maxY) }

                  // grid + y labels
                  ctx.strokeStyle = Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.10)
                  ctx.fillStyle = Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.45)
                  ctx.lineWidth = 1
                  ctx.font = "10px " + Style.font.family
                  ctx.textAlign = "right"
                  var steps = 4
                  for (var i = 0; i <= steps; i++) {
                    var val = Math.round(maxY * i / steps)
                    var y = yOf(val)
                    ctx.beginPath()
                    ctx.moveTo(padL, y)
                    ctx.lineTo(padL + w, y)
                    ctx.stroke()
                    ctx.fillText(String(val), padL - 6, y + 3)
                  }

                  // ideal line
                  var total0 = pts.length > 0 ? pts[0].total : maxY
                  ctx.strokeStyle = Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.28)
                  ctx.setLineDash([4, 4])
                  ctx.beginPath()
                  ctx.moveTo(xOf(start), yOf(total0))
                  ctx.lineTo(xOf(end), yOf(0))
                  ctx.stroke()
                  ctx.setLineDash([])

                  // actual line
                  ctx.strokeStyle = Color.accent
                  ctx.lineWidth = 2
                  ctx.beginPath()
                  for (var p = 0; p < pts.length; p++) {
                    var px = xOf(pts[p].atMs), py = yOf(pts[p].remaining)
                    if (p === 0) ctx.moveTo(px, py)
                    else ctx.lineTo(px, py)
                  }
                  ctx.stroke()

                  ctx.fillStyle = Color.accent
                  for (var q = 0; q < pts.length; q++) {
                    ctx.beginPath()
                    ctx.arc(xOf(pts[q].atMs), yOf(pts[q].remaining), 2.5, 0, Math.PI * 2)
                    ctx.fill()
                  }

                  // today
                  var now = Date.now()
                  if (now > start && now < end) {
                    var nx = xOf(now)
                    ctx.strokeStyle = Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.55)
                    ctx.setLineDash([2, 3])
                    ctx.beginPath()
                    ctx.moveTo(nx, padT)
                    ctx.lineTo(nx, padT + h)
                    ctx.stroke()
                    ctx.setLineDash([])
                  }

                  // x labels
                  ctx.fillStyle = Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.45)
                  ctx.textAlign = "left"
                  ctx.fillText(Qt.formatDateTime(new Date(start), "d MMM"), padL, height - 8)
                  ctx.textAlign = "right"
                  ctx.fillText(Qt.formatDateTime(new Date(end), "d MMM"), padL + w, height - 8)
                }
              }

              Text {
                width: parent.width
                visible: reportsView.points().length <= 1
                text: reportsView.sprintId === ""
                  ? t("reports.pickSprint")
                  : t("reports.noBurndownData")
                color: Qt.darker(Color.foreground, 1.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap
              }
            }
          }

          // ---- velocity
          Rectangle {
            width: parent.width
            height: velocityCol.height + 28
            radius: 10
            color: Qt.darker(Color.background, 1.06)
            border.color: Util.alpha(Color.foreground, 0.08)
            border.width: 1

            Column {
              id: velocityCol
              x: 14
              y: 14
              width: parent.width - 28
              spacing: 8

              Text {
                text: t("reports.velocity")
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.bold: true
              }
              Text {
                width: parent.width
                text: {
                  var v = (reportsView.report && reportsView.report.velocity) ? reportsView.report.velocity : []
                  if (v.length === 0) return t("reports.noVelocity")
                  var names = []
                  for (var i = 0; i < v.length; i++) names.push(v[i].name + ": " + v[i].completed + "/" + v[i].estimates)
                  return names.join("   ·   ")
                }
                color: Qt.darker(Color.foreground, 1.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap
              }
            }
          }

          // ---- issue lists
          Row {
            width: parent.width
            spacing: Style.space(10)

            Rectangle {
              width: (parent.width - Style.space(10)) / 2
              height: Math.max(120, doneCol.height + 28)
              radius: 10
              color: Qt.darker(Color.background, 1.06)
              border.color: Util.alpha(Color.foreground, 0.08)
              border.width: 1

              Column {
                id: doneCol
                x: 14
                y: 14
                width: parent.width - 28
                spacing: 5

                Text {
                  text: t("reports.completedInSprint")
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Repeater {
                  model: reportsView.summary().completedKeys || []
                  Text {
                    required property var modelData
                    text: modelData + "  " + reportsView.summaryOf(modelData)
                    width: doneCol.width
                    elide: Text.ElideRight
                    color: Qt.darker(Color.foreground, 1.25)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: reportsView.app.openIssue(modelData)
                    }
                  }
                }
              }
            }

            Rectangle {
              width: (parent.width - Style.space(10)) / 2
              height: Math.max(120, leftCol.height + 28)
              radius: 10
              color: Qt.darker(Color.background, 1.06)
              border.color: Util.alpha(Color.foreground, 0.08)
              border.width: 1

              Column {
                id: leftCol
                x: 14
                y: 14
                width: parent.width - 28
                spacing: 5

                Text {
                  text: t("reports.leftInSprint")
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Repeater {
                  model: reportsView.summary().notCompletedKeys || []
                  Text {
                    required property var modelData
                    text: modelData + "  " + reportsView.summaryOf(modelData)
                    width: leftCol.width
                    elide: Text.ElideRight
                    color: Qt.darker(Color.foreground, 1.25)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: reportsView.app.openIssue(modelData)
                    }
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
