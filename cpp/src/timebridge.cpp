#include "timebridge.h"

#include <QLocale>
#include <QTime>

QString TimeBridge::currentTimeString(bool includeSeconds) const {
    const auto format =
        includeSeconds ? QLocale::LongFormat : QLocale::ShortFormat;
    return QLocale().toString(QTime::currentTime(), format);
}
