import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    property alias cfg_userText: userTextField.text
    property alias cfg_showSeconds: showSecondsCheckBox.checked

    Kirigami.FormLayout {
        QQC2.TextField {
            id: userTextField

            Kirigami.FormData.label: i18n("Display Text:")
            placeholderText: i18n("Enter text here")
        }

        QQC2.CheckBox {
            id: showSecondsCheckBox

            Kirigami.FormData.label: i18n("Options:")
            text: i18n("Show Seconds")
        }
    }
}
