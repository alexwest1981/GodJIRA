import QtQuick
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "GodJIRA.plugin"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: " "
    labelVisible: false
    tooltipText: root.t("bar.openTooltip")
    fixedWidth: Math.max(46, root.bar ? root.bar.barSize : 46)
    fixedHeight: Math.max(26, root.bar ? root.bar.barSize : 26)

    Image {
      anchors.centerIn: parent
      width: 44
      height: 23
      source: Qt.resolvedUrl("./assets/godzilla.svg")
      sourceSize.width: 192
      sourceSize.height: 96
      smooth: true
      mipmap: true
    }

    onPressed: function(button) {
      if (!root.bar) return
      if (button === Qt.RightButton) {
        // Högerklick: fråga tavlan NU i stället för att vänta på minutklockan.
        root.watchTick()
      } else {
        // Baren är en dörr, inte en andra panel. Appen är webbpanelen (cockpiten,
        // körningarna, kartan, filerna) och den får inte en andra upplaga i QML --
        // den upplagan stod still medan allt nytt byggdes.
        // Adressen står i panel/godjira.desktop (install.sh fyller i @PORT@), så
        // klicket går genom menyns egen post: samma port, samma fönsterregel.
        root.bar.run("sh -c 'gtk-launch godjira.desktop 2>/dev/null || xdg-open http://127.0.0.1:8788'")
      }
    }
  }

  // Widgeten är barens hela del av GodJIRA: märket, notisbevakningen och dörren in.
  // Panelens nio vyer bodde förr här också (JiraPanel + views + components, ~6600 rader
  // QML) och gjorde samma sak som webbpanelen -- de är rivna.

  // ------------------------------------------------------------- watcher
  // Polls the bridge for board changes and raises a desktop notification when
  // something moved / was added / updated / removed on a tracked board. The
  // bridge keeps a baseline on disk, so only real (external) changes notify —
  // the user's own edits inside the app bump the baseline and stay quiet.
  readonly property string bridgePath: {
    var url = Qt.resolvedUrl("./bin/jira_bridge.py").toString()
    return url.replace(/^file:\/\//, "")
  }

  // Baren har ingen panel att fråga, så den hämtar samma texttabell själv —
  // en gång, och på nytt om shellen laddas om.
  property var strings: ({})

  function t(key) {
    var s = root.strings ? root.strings[key] : undefined
    return (s === undefined || s === null || s === "") ? key : s
  }

  Process {
    id: stringsProc
    command: ["python3", root.bridgePath, "strings"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          if (parsed && parsed.ok) root.strings = parsed.strings || ({})
        } catch (e) {
          // tooltip faller tillbaka på nyckeln; inget att göra här
        }
      }
    }
  }

  Component.onCompleted: stringsProc.running = true

  Process {
    id: watchProc
    command: ["python3", root.bridgePath, "watch"]
  }

  function watchTick() {
    if (watchProc.running) return
    watchProc.running = true
  }

  Timer {
    id: watchTimer
    interval: 30000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.watchTick()
  }
}
