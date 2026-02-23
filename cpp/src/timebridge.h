#pragma once

#include <qqmlintegration.h>

#include <QObject>
#include <QString>

class TimeBridge : public QObject {
    Q_OBJECT
    QML_ELEMENT

   public:
    using QObject::QObject;

    Q_INVOKABLE QString currentTimeString(bool includeSeconds) const;
};
