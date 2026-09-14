import QtQuick
import qs.Commons
import qs.Ui

// Inställningar: språk, anslutning, startvy och om. Allt som rör anslutningen
// bor här i stället för bakom en post man kunde snubbla in på — att öppna vyn
// ändrar ingenting, och fälten för site/token finns inte ens i scenen.
Item {
  id: settingsView
  anchors.fill: parent

  property var app: null
  property bool disconnectConfirm: false

  function t(key, args) { return app ? app.t(key, args) : key }

  function siteUrl() {
    if (!app) return ""
    return app.account.siteUrl || (app.connectionInfo ? app.connectionInfo.siteUrl : "") || ""
  }

  function accountLine() {
    if (!app) return ""
    var name = app.account.displayName || ""
    var email = app.account.email || (app.connectionInfo ? app.connectionInfo.email : "") || ""
    if (name && email) return t("conn.account", { name: name, email: email })
    return name || email
  }

  function modeLine() {
    return t("conn.mode", { mode: app ? app.connectionModeLabel() : "" })
  }

  function tokenLine() {
    var has = app && app.connectionInfo ? app.connectionInfo.hasToken === true : false
    return t("conn.token", { state: has ? t("conn.tokenStored") : t("conn.tokenMissing") })
  }

  function languageLabel() {
    if (!app) return ""
    if (!app.languageSetting) return t("settings.languageAuto")
    for (var i = 0; i < app.languageNames.length; i++) {
      if (app.languageNames[i].code === app.languageSetting) return app.languageNames[i].native
    }
    return app.language
  }

  Column {
    anchors.fill: parent
    spacing: 0

    Rectangle {
      width: parent.width
      height: 52
      color: "transparent"

      Text {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        text: settingsView.t("nav.settings")
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.bold: true
      }
      Text {
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        text: settingsView.t("settings.language") + ": " + settingsView.languageLabel()
        color: Qt.darker(Color.foreground, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }

    Flickable {
      width: parent.width
      height: parent.height - 52
      clip: true
      contentWidth: width
      contentHeight: body.height + 24
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: body
        width: parent.width - 32
        x: 16
        spacing: Style.space(18)

        // ---------------------------------------------------------- språk
        Text {
          text: settingsView.t("settings.language")
          color: Qt.darker(Color.foreground, 1.35)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }

        Flow {
          width: parent.width
          spacing: Style.space(6)

          Button {
            text: settingsView.t("settings.languageAuto")
            bordered: true
            fontSize: Style.font.caption
            horizontalPadding: Style.space(8)
            selected: !!(app && app.languageSetting === "")
            tooltipText: settingsView.t("settings.languageTooltip")
            onClicked: if (app) app.setLanguage("")
          }

          Repeater {
            model: app ? app.languageNames : []

            Button {
              required property var modelData
              text: modelData.native
              bordered: true
              fontSize: Style.font.caption
              horizontalPadding: Style.space(8)
              selected: !!(app && app.languageSetting === modelData.code)
              tooltipText: modelData.code
              onClicked: if (app) app.setLanguage(modelData.code)
            }
          }
        }

        Text {
          width: parent.width
          wrapMode: Text.Wrap
          text: settingsView.t("settings.issueLanguageNote")
          color: Qt.darker(Color.foreground, 1.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        // ----------------------------------------------------- anslutning
        Text {
          text: settingsView.t("settings.connection")
          color: Qt.darker(Color.foreground, 1.35)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }

        Text {
          width: parent.width
          wrapMode: Text.Wrap
          text: settingsView.t("conn.locked")
          color: Qt.darker(Color.foreground, 1.35)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Rectangle {
          width: parent.width
          height: infoCol.implicitHeight + 24
          radius: 10
          color: Qt.darker(Color.background, 1.15)
          border.color: Util.alpha(Color.foreground, 0.08)
          border.width: 1

          Column {
            id: infoCol
            width: parent.width - 24
            x: 12
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(6)

            Text {
              text: settingsView.siteUrl()
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }
            Text {
              text: settingsView.accountLine()
              color: Qt.darker(Color.foreground, 1.3)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Text {
              text: settingsView.modeLine()
              color: Qt.darker(Color.foreground, 1.3)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Text {
              text: settingsView.tokenLine()
              color: Qt.darker(Color.foreground, 1.3)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Button {
            text: settingsView.t("conn.create")
            bordered: true
            tooltipText: settingsView.t("conn.createTooltip")
            onClicked: if (app) app.openConnectForm()
          }

          Button {
            text: settingsView.disconnectConfirm
              ? settingsView.t("conn.disconnectArmed") : settingsView.t("conn.disconnect")
            bordered: true
            selected: settingsView.disconnectConfirm
            tooltipText: settingsView.t("conn.disconnectTooltip")
            onClicked: {
              if (!settingsView.disconnectConfirm) {
                settingsView.disconnectConfirm = true
                return
              }
              settingsView.disconnectConfirm = false
              if (app) app.logout()
            }
          }
        }

        Text {
          width: parent.width
          wrapMode: Text.Wrap
          text: settingsView.t("conn.disconnectHint")
          color: Qt.darker(Color.foreground, 1.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        // -------------------------------------------------------- startvy
        Text {
          text: settingsView.t("settings.startView")
          color: Qt.darker(Color.foreground, 1.35)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }

        Flow {
          width: parent.width
          spacing: Style.space(6)

          Repeater {
            model: app ? app.navModel : []

            Button {
              required property var modelData
              readonly property bool isCurrent: {
                if (!app) return false
                var want = app.configStartView || "board"
                return want === modelData.key
              }
              visible: modelData.key !== "settings"
              text: settingsView.t(modelData.label)
              bordered: true
              fontSize: Style.font.caption
              horizontalPadding: Style.space(8)
              selected: isCurrent
              tooltipText: settingsView.t("settings.startViewTooltip")
              onClicked: if (app) app.setStartView(modelData.key === "board" ? "" : modelData.key)
            }
          }
        }

        // ------------------------------------------------------------ om
        Text {
          text: settingsView.t("settings.about")
          color: Qt.darker(Color.foreground, 1.35)
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }

        Column {
          width: parent.width
          spacing: Style.space(4)

          Text {
            text: settingsView.t("settings.version", { version: app ? app.pluginVersion : "" })
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Text {
            text: settingsView.t("settings.configFile") + " ~/.config/omarchy/jira.json"
            color: Qt.darker(Color.foreground, 1.4)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Text {
            text: settingsView.t("settings.keyring")
            color: Qt.darker(Color.foreground, 1.4)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }

        Item { width: 1; height: 8 }
      }
    }
  }
}
