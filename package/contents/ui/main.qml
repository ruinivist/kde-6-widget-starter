import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import "cppbridge" as CppBridge
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    // Properties accessed from configuration.
    // "qmllint" sees `plasmoid` as QObject here, but Plasma injects `configuration` at runtime.
    // qmllint disable missing-property
    property bool showSeconds: plasmoid.configuration.showSeconds
    // qmllint disable missing-property
    property string userText: plasmoid.configuration.userText
    property string cppTimeText: "Click the button to request time from C++."

    // Widget Setup
    width: Kirigami.Units.gridUnit * 20
    height: Kirigami.Units.gridUnit * 20
    toolTipMainText: "Plasma 6 Starter"
    toolTipSubText: "Click to open full view"

    CppBridge.TimeBridge {
        id: timeBridge
    }

    // Define the full representation (popup or desktop widget)
    fullRepresentation: Item {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 10
        Layout.minimumHeight: Kirigami.Units.gridUnit * 10
        Layout.preferredWidth: root.width
        Layout.preferredHeight: root.height

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.largeSpacing

            PlasmaComponents.Label {
                text: root.userText
                font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.5
                Layout.alignment: Qt.AlignHCenter
            }

            PlasmaComponents.Label {
                text: "Welcome to 6!"
                Layout.alignment: Qt.AlignHCenter
                opacity: 0.7
            }

            PlasmaComponents.Label {
                text: root.cppTimeText
                Layout.alignment: Qt.AlignHCenter
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }

            Item {
                Layout.fillHeight: true
            }

            PlasmaComponents.Button {
                text: "Get from C++"
                Layout.alignment: Qt.AlignHCenter
                onClicked: root.cppTimeText = timeBridge.currentTimeString(root.showSeconds)
            }

        }

    }

    // Define the compact representation (icon in panel)
    compactRepresentation: Item {
        Kirigami.Icon {
            anchors.fill: parent
            source: "plasma"

            MouseArea {
                anchors.fill: parent
                onClicked: root.expanded = !root.expanded
            }

        }

    }

}
