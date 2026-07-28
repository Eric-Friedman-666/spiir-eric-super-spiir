#ifndef __CRASHCAR_SINGLEFAR_H__
#define __CRASHCAR_SINGLEFAR_H__
#include <gst/gst.h>
typedef struct { GstElement *owner; gboolean enabled; } CrashcarSingleFarEngine;
void crashcar_singlefar_engine_init(CrashcarSingleFarEngine *, GstElement *); void crashcar_singlefar_engine_clear(CrashcarSingleFarEngine *);
gboolean crashcar_singlefar_engine_start(CrashcarSingleFarEngine *); GstFlowReturn crashcar_singlefar_engine_transform_ip(CrashcarSingleFarEngine *, GstBuffer *);
#endif
