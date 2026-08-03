import QtQuick
import cppbridge

Item {
    TimeBridge {
        id: bridge
    }

    Component.onCompleted: {
        if (bridge.currentTimeString(false).length === 0) {
            Qt.exit(1)
        }
        Qt.exit(0)
    }
}
