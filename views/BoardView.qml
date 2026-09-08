import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: boardView
  anchors.fill: parent

  property var app: null
  property int rev: app ? app.snapshotRev : -1
  property string selBoard: app ? app.selectedBoardId : ""
  property string selKey: app ? app.selectedIssueKey : ""
  property var cols: []
  property string boardTitle: ""
  property string boardInfo: ""
  property bool canAdd: false
  property bool canDelete: false

  // drag + drop state
  property bool dragging: false
  property string dragKey: ""
  property var dragGhostItem: dragGhost
  property var dragHintItem: dragHint
  property int hoverIndex: -1
  property int dropAttempts: 0

  // Columns share the visible width so the whole board fits whatever size the
  // floating window is. Below a minimum width they hold their size and the
  // board scrolls horizontally instead of squashing cards to nothing.
  property int minColumnWidth: 170

  function columnWidth() {
    var n = boardView.cols.length
    if (n === 0) return 300
    var gapSum = colsRow.spacing * Math.max(0, n - 1)
    return Math.max(boardView.minColumnWidth, (colsRow.width - gapSum) / n)
  }

  function boardContentWidth() {
    var n = boardView.cols.length
    if (n === 0) return boardScroll.width
    return Math.max(boardScroll.width,
      n * boardView.minColumnWidth + colsRow.spacing * Math.max(0, n - 1))
  }

  function accentForName(name) {
    var n = String(name || "").toLowerCase()
    if (n.indexOf("done") !== -1 || n.indexOf("closed") !== -1) return "#4f9d69"
    if (n.indexOf("review") !== -1) return "#8a63c4"
    if (n.indexOf("block") !== -1 || n.indexOf("wait") !== -1) return "#c05555"
    if (n.indexOf("progress") !== -1 || n.indexOf("dev") !== -1 || n.indexOf("in ") !== -1) return "#4a8fd6"
    return "#c9a227"
  }

  // The theme may give surfaces an alpha channel (glass over blur). Popups that
  // host forms need a fully opaque surface or the board behind bleeds through.
  function opaqueColor(c) {
    var s = String(c)
    if (s.length === 9 && s.charAt(0) === "#") return "#" + s.substr(3)
    return s
  }

  function build() {
    if (!app) { cols = []; boardTitle = ""; boardInfo = ""; canAdd = false; canDelete = false; return }
    var board = app.currentBoard()
    if (!board) { cols = []; boardTitle = ""; boardInfo = ""; canAdd = false; canDelete = false; return }

    boardTitle = (board.projectKey || "") + " · " + board.name
    boardInfo = board.type === "scrum" && board.sprint
      ? app.sprintLabel(board)
      : "Kanban"
    canAdd = app.boardCanAdd()
    canDelete = app.boardCanDelete()

    var out = []
    var seen = {}
    var src = (board.columns || []).slice()

    for (var c = 0; c < src.length; c++) {
      var colName = src[c].name || src[c].statusName || ("Kolumn " + (c + 1))
      out.push({ title: colName, statusId: String(src[c].statusId || ""), accent: accentForName(colName), list: [] })
      seen[colName] = out.length - 1
    }
    for (var i = 0; i < (board.issues || []).length; i++) {
      var iss = board.issues[i]
      var nm = iss.statusName || ""
      if (seen[nm] === undefined) {
        out.push({ title: nm, statusId: String(iss.statusId || ""), accent: accentForName(nm), list: [] })
        seen[nm] = out.length - 1
      }
      out[seen[nm]].list.push(iss)
    }
    cols = out

    if (createPage.visible) createPage.visible = false

    if (app.selectedIssueKey) {
      var found = false
      for (var x = 0; x < out.length; x++)
        for (var y = 0; y < out[x].list.length; y++)
          if (out[x].list[y].key === app.selectedIssueKey) { found = true; break }
      if (!found) app.clearSelectionIfOffBoard()
    }
  }

  function columnRects() {
    var rects = []
    for (var i = 0; i < colsRow.children.length; i++) {
      var ch = colsRow.children[i]
      if (ch && ch.isColumn === true) {
        var p = ch.mapToItem(boardArea, 0, 0)
        rects.push({ index: rects.length, x: p.x, y: p.y, w: ch.width, h: ch.height })
      }
    }
    return rects
  }

  function colIndexAt(cx, cy) {
    var rects = columnRects()
    for (var i = 0; i < rects.length; i++) {
      if (cx >= rects[i].x && cx <= rects[i].x + rects[i].w &&
          cy >= rects[i].y && cy <= rects[i].y + rects[i].h) {
        return rects[i].index
      }
    }
    return -1
  }

  // ---- drag handling (driven by IssueCard)
  function prepareDrag(issue, mouseArea, mouse) {
    if (dragging || !issue || !app) return
    dragKey = issue.key
    var pt = mouseArea.mapToItem(boardArea, mouse.x, mouse.y)
    dragGhost.width = Math.max(220, mouseArea.width + 10)
    dragGhost.height = mouseArea.height + 6
    dragGhost.x = pt.x - dragGhost.width / 2
    dragGhost.y = pt.y - dragGhost.height / 2
    dragGhost.keyText = issue.key
    dragGhost.summaryText = issue.summary || ""
    dragGhost.accent = app.statusColor(issue)
  }

  function onCardDrag(active, key) {
    if (active) {
      if (key !== dragKey) return
      dragging = true
      dragGhost.visible = true
      updateHint()
    } else {
      var wasDragging = dragging
      dragging = false
      if (wasDragging && key === dragKey) {
        finalizeDrag()
      }
    }
  }

  function updateHint() {
    if (!dragging || !dragGhost.visible) return
    var idx = colIndexAt(dragGhost.x + dragGhost.width / 2, dragGhost.y + dragGhost.height / 2)
    setHint(idx)
  }

  function setHint(idx) {
    if (idx === hoverIndex) return
    hoverIndex = idx
    if (idx >= 0 && cols.length > idx) {
      var rects = columnRects()
      if (idx < rects.length) {
        dragHint.visible = true
        dragHint.x = rects[idx].x
        dragHint.y = rects[idx].y
        dragHint.width = rects[idx].w
        dragHint.height = rects[idx].h
        dragHint.color = Util.alpha(cols[idx].accent, 0.10)
        dragHint.border.color = cols[idx].accent
        return
      }
    }
    dragHint.visible = false
  }

  function clearDragVisuals() {
    dragGhost.visible = false
    dragHint.visible = false
    hoverIndex = -1
  }

  function finalizeDrag() {
    var key = dragKey
    var targetIdx = hoverIndex
    dragKey = ""
    clearDragVisuals()

    if (key === "" || targetIdx < 0 || !app || targetIdx >= cols.length) return
    var col = cols[targetIdx]
    var issue = app.issueByKey(key)
    if (!issue || !col) return
    if (String(issue.statusName || "").toLowerCase() === String(col.title || "").toLowerCase()) return

    // Resolve the transition that lands in this column, then move.
    dropResolveCol = col
    dropResolveKey = key
    dropAttempts = 0
    app.refreshTransitions(key)
    dropTimer.restart()
  }

  property var dropResolveCol: null
  property string dropResolveKey: ""

  function resolveTransition(transitions) {
    var col = dropResolveCol
    var match = null
    for (var i = 0; i < transitions.length; i++) {
      var tr = transitions[i]
      if (col.statusId && tr.toStatusId && String(tr.toStatusId) === String(col.statusId)) { match = tr; break }
      if (!match && (tr.toStatusName || "").toLowerCase() === String(col.title || "").toLowerCase()) match = tr
    }
    return match
  }

  function restoreTransitions() {
    if (app && app.selectedIssueKey) {
      app.refreshTransitions(app.selectedIssueKey)
    } else {
      app.issueTransitions = []
      app.issueTransitionsFor = ""
    }
  }

  function abortDrop(reason) {
    dropTimer.stop()
    if (reason) app.notice = reason
    restoreTransitions()
  }

  function finishDrop() {
    dropTimer.stop()
    restoreTransitions()
  }

  // ---- "flytta till" via drag drop resolves asynchronously after transitions
  Timer {
    id: dropTimer
    interval: 120
    repeat: true
    running: false
    onTriggered: {
      if (!app) { abortDrop("") ; return }
      dropAttempts++
      if (!app.transitionsLoading && app.issueTransitionsFor === dropResolveKey && app.issueTransitions.length > 0) {
        var match = boardView.resolveTransition(app.issueTransitions)
        if (match) {
          app.moveIssue(dropResolveKey, match.toStatusId || match.toStatusName)
          finishDrop()
        } else {
          abortDrop("Kan inte flytta till '" + dropResolveCol.title + "' – ingen giltig statusändring.")
        }
        return
      }
      if (!app.transitionsLoading && app.issueTransitionsFor === dropResolveKey && app.issueTransitions.length === 0) {
        abortDrop("Kan inte flytta till '" + dropResolveCol.title + "' – inga statusändringar tillgängliga.")
        return
      }
      if (dropAttempts > 30) abortDrop("Statusändringarna svarade inte – försök igen.")
    }
  }

  onRevChanged: build()
  onSelBoardChanged: build()
  onSelKeyChanged: if (app && !app.selectedIssueKey) build()
  Component.onCompleted: build()

  Column {
    anchors.fill: parent
    spacing: 0

    // ---- top bar
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
          text: boardTitle
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: boardInfo
          color: Qt.darker(Color.foreground, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Row {
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(10)

        Button {
          id: newIssueButton
          text: "＋ Ny"
          fontSize: Style.font.bodySmall
          visible: boardView.canAdd && app && app.snapshot !== null
          tooltipText: "Skapa ett ärende på tavlan"
          onClicked: openCreatePage()
        }

        Dropdown {
          id: boardPicker
          width: 230
          label: "Tavla"
          options: app ? app.boardsList() : []
          value: app ? String(app.selectedBoardId) : ""
          onChanged: function(v) {
            app.selectedBoardId = v
          }
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
          id: boardArea
          width: parent.width
          height: parent.height
          color: "transparent"
          clip: true

          Text {
            anchors.centerIn: parent
            text: app && app.snapshot
              ? "Ingen tavla vald – välj i listan uppe till höger."
              : "Läser in tavlor…"
            color: Qt.darker(Color.foreground, 1.5)
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            visible: !app || !app.snapshot || cols.length === 0
          }

          Flickable {
            id: boardScroll
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.topMargin: 4
            anchors.bottomMargin: 8
            clip: true
            contentWidth: colsRow.width
            contentHeight: colsRow.height
            boundsBehavior: Flickable.StopAtBounds

            Row {
              id: colsRow
              width: boardView.boardContentWidth()
              height: boardScroll.height
              spacing: Style.space(10)

              Repeater {
                model: boardView.cols

                Rectangle {
                  required property var modelData
                  property bool isColumn: true

                  width: boardView.columnWidth()
                  height: colsRow.height
                  color: "transparent"
                  border.color: Util.alpha(Color.foreground, 0.07)
                  border.width: 1
                  radius: 10

                  Rectangle {
                    width: parent.width
                    height: 2
                    radius: 1
                    anchors.top: parent.top
                    color: modelData.accent
                  }

                  // column header: dot, title (elided), count. Anchored rather
                  // than a Row so narrow columns cannot push the count out.
                  Item {
                    anchors.top: parent.top
                    anchors.topMargin: 10
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    height: 22

                    Rectangle {
                      width: 8; height: 8; radius: 4
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                      color: modelData.accent
                    }
                    Text {
                      id: colTitleText
                      anchors.left: parent.left
                      anchors.leftMargin: 14
                      anchors.right: colCountText.left
                      anchors.rightMargin: 6
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.title
                      color: Color.foreground
                      font.family: Style.font.family
                      font.pixelSize: Style.font.bodySmall
                      font.bold: true
                      elide: Text.ElideRight
                    }
                    Text {
                      id: colCountText
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.list.length
                      color: Qt.darker(Color.foreground, 1.4)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  Rectangle {
                    id: listFrame
                    anchors.top: parent.top
                    anchors.topMargin: 34
                    anchors.left: parent.left
                    anchors.leftMargin: 6
                    anchors.right: parent.right
                    anchors.rightMargin: 6
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 6
                    radius: 8
                    color: Qt.darker(Color.background, 1.15)
                    clip: true

                    ListView {
                      anchors.fill: parent
                      model: modelData.list
                      spacing: 4
                      clip: true
                      boundsBehavior: Flickable.StopAtBounds

                      delegate: Item {
                        required property var modelData
                        required property int index
                        width: ListView.view.width
                        height: 46

                        Loader {
                          id: cardLoader
                          anchors.fill: parent
                          anchors.leftMargin: 4
                          anchors.topMargin: 1
                          anchors.rightMargin: 4
                          anchors.bottomMargin: 1
                          source: "../components/IssueCard.qml"
                          onLoaded: {
                            item.app = boardView.app
                            item.host = boardView
                            item.issue = modelData
                            item.colWidth = Qt.binding(function() { return cardLoader.width })
                          }
                        }
                      }

                      Text {
                        anchors.centerIn: parent
                        text: "Inga ärenden här"
                        color: Qt.darker(Color.foreground, 1.6)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        visible: parent.count === 0
                      }
                    }
                  }
                }
              }
            }
          }

          // ghost shown while a card is dragged between columns
          Rectangle {
            id: dragGhost
            visible: false
            z: 50
            radius: 8
            color: Qt.darker(Color.background, 1.3)
            border.width: 2
            border.color: dragGhost.accent
            property string accent: "#4a8fd6"
            property string keyText: ""
            property string summaryText: ""

            onXChanged: boardView.updateHint()
            onYChanged: boardView.updateHint()

            Column {
              anchors.fill: parent
              anchors.margins: 8
              spacing: 2

              Text {
                text: dragGhost.keyText
                color: dragGhost.accent
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
              }
              Text {
                width: parent.width
                text: dragGhost.summaryText
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
                elide: Text.ElideRight
              }
            }
          }

          // highlight of the column currently hovered while dragging
          Rectangle {
            id: dragHint
            visible: false
            z: 40
            radius: 10
            border.width: 2
            color: Util.alpha(Color.foreground, 0.05)
          }
        }

      }


    }
  }

  function openCreatePage() {
    addErrorText.text = ""
    addSummary.text = ""
    addStatus.options = addStatusOptions()
    if (addStatus.options.length > 0) addStatus.value = String(addStatus.options[0].value)
    createPage.visible = true
    Qt.callLater(function() { addSummary.forceActiveFocus() })
  }

  // Full opaque "page" for creating an issue. It replaces the whole board view
  // (own tab feel), so nothing from the board can bleed into the form.
  Rectangle {
    id: createPage
    visible: false
    z: 90
    anchors.fill: parent
    color: boardView.opaqueColor(Color.background)
    property bool backHover: false

    Column {
      anchors.top: parent.top
      anchors.left: parent.left
      anchors.margins: 30
      width: 480
      spacing: Style.space(14)

      Item {
        width: backTxt.implicitWidth + 8
        height: backTxt.implicitHeight + 4
        Text {
          id: backTxt
          anchors.centerIn: parent
          text: "← Tillbaka"
          color: createPage.backHover ? Color.foreground : Qt.darker(Color.foreground, 1.3)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        MouseArea {
          anchors.fill: parent
          hoverEnabled: true
          onEntered: createPage.backHover = true
          onExited: createPage.backHover = false
          onClicked: createPage.visible = false
        }
      }

      Text {
        text: "Nytt ärende"
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.bold: true
      }

      Text {
        text: "Tavla: " + boardTitle + (boardInfo !== "" ? "  ·  " + boardInfo : "")
        color: Qt.darker(Color.foreground, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
        width: parent.width
      }

      Rectangle {
        width: parent.width
        height: 1
        color: Util.alpha(Color.foreground, 0.12)
      }

      Dropdown {
        id: addStatus
        width: parent.width
        label: "Status"
        options: addStatusOptions()
      }

      TextField {
        id: addSummary
        width: parent.width
        placeholderText: "Sammanfattning"
        onAccepted: saveNewIssue()
      }

      Text {
        id: addErrorText
        width: parent.width
        text: ""
        color: Color.urgent
        wrapMode: Text.Wrap
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        visible: text !== ""
      }

      Row {
        spacing: Style.space(8)

        Button {
          text: "Spara"
          bordered: true
          onClicked: saveNewIssue()
        }
        Button {
          text: "Avbryt"
          onClicked: createPage.visible = false
        }
      }
    }
  }


  // Full-page issue viewer: clicking a card replaces the board with the issue
  // (read content, then pick a status). Same opaque-page approach as create.
  Rectangle {
    id: detailPage
    visible: boardView.selKey !== ""
    z: 90
    anchors.fill: parent
    color: boardView.opaqueColor(Color.background)

    Loader {
      id: detailLoader
      anchors.fill: parent
      anchors.topMargin: 30
      source: Qt.resolvedUrl("../components/IssueDetail.qml")
      onLoaded: {
        item.app = Qt.binding(function() { return boardView.app })
      }
    }
  }


  function addStatusOptions() {
    var opts = []
    for (var i = 0; i < cols.length; i++) {
      opts.push({ value: String(cols[i].statusId || cols[i].title), label: cols[i].title })
    }
    return opts
  }

  function saveNewIssue() {
    addErrorText.text = ""
    var summary = addSummary.text
    if (!summary || !String(summary).trim()) {
      addErrorText.text = "Skriv en sammanfattning."
      return
    }
    var statusId = addStatus.value
    if (!statusId && cols.length > 0) statusId = String(cols[0].statusId || "")
    createPage.visible = false
    app.createIssue(app.selectedBoardId, summary, statusId)
  }
}
