#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint16_t status;
    char *content_type;
    unsigned char *body;
    size_t body_len;
    bool cache;
    char *error;
    char *headers_json;
} TcResponse;

extern TcResponse *tc_http_request(const char *, const char *, const char *);
extern TcResponse *tc_http_request_full(const char *, const char *, const char *, const char *,
                                        const unsigned char *, size_t);
extern void tc_response_free(TcResponse *);
extern void tc_string_free(char *);
extern char *tc_data_operation(const char *);
extern TcResponse *tc_session_http_request_full(const void *, const char *, const char *,
                                                const char *, const unsigned char *, size_t);

static PyObject *response_to_python(TcResponse *response, int full) {
    if (response == NULL)
        return PyErr_NoMemory();
    if (response->error != NULL) {
        PyErr_SetString(PyExc_RuntimeError, response->error);
        tc_response_free(response);
        return NULL;
    }
    PyObject *status = PyLong_FromLong(response->status);
    PyObject *content_type = PyUnicode_FromString(response->content_type);
    PyObject *body =
        PyBytes_FromStringAndSize((const char *)response->body, (Py_ssize_t)response->body_len);
    PyObject *cache = PyBool_FromLong(response->cache);
    PyObject *response_headers = full ? PyUnicode_FromString(response->headers_json) : NULL;
    if (status == NULL || content_type == NULL || body == NULL || cache == NULL ||
        (full && response_headers == NULL)) {
        Py_XDECREF(status);
        Py_XDECREF(content_type);
        Py_XDECREF(body);
        Py_XDECREF(cache);
        Py_XDECREF(response_headers);
        tc_response_free(response);
        return NULL;
    }
    PyObject *result = full ? PyTuple_Pack(5, status, content_type, body, cache, response_headers)
                            : PyTuple_Pack(4, status, content_type, body, cache);
    Py_DECREF(status);
    Py_DECREF(content_type);
    Py_DECREF(body);
    Py_DECREF(cache);
    Py_XDECREF(response_headers);
    tc_response_free(response);
    return result;
}

static PyObject *request_common(PyObject *self, PyObject *args, int full) {
    (void)self;
    const char *database;
    const char *method;
    const char *url;
    const char *headers = "{}";
    const char *request_body = "";
    Py_ssize_t request_body_len = 0;
    if (full) {
        if (!PyArg_ParseTuple(args, "ssssy#:request_full", &database, &method, &url, &headers,
                              &request_body, &request_body_len))
            return NULL;
    } else if (!PyArg_ParseTuple(args, "sss:request", &database, &method, &url))
        return NULL;
    TcResponse *response;
    Py_BEGIN_ALLOW_THREADS response =
        tc_http_request_full(database, method, url, headers, (const unsigned char *)request_body,
                             (size_t)request_body_len);
    Py_END_ALLOW_THREADS if (response == NULL) { return PyErr_NoMemory(); }
    return response_to_python(response, full);
}

static PyObject *native_request(PyObject *self, PyObject *args) {
    return request_common(self, args, 0);
}
static PyObject *native_request_full(PyObject *self, PyObject *args) {
    return request_common(self, args, 1);
}

static PyObject *native_data_operation(PyObject *self, PyObject *args) {
    (void)self;
    const char *request;
    if (!PyArg_ParseTuple(args, "s:data_operation", &request))
        return NULL;
    char *response;
    Py_BEGIN_ALLOW_THREADS response = tc_data_operation(request);
    Py_END_ALLOW_THREADS if (response == NULL) return PyErr_NoMemory();
    PyObject *result = PyUnicode_FromString(response);
    tc_string_free(response);
    return result;
}

extern void *tc_session_open(const char *, char **);
extern char *tc_session_operation(const void *, const char *);
extern void tc_session_free(void *);

static void session_destroy(PyObject *capsule) {
    void *session = PyCapsule_GetPointer(capsule, "rcfp.session");
    if (session != NULL)
        tc_session_free(session);
}
static PyObject *session_open(PyObject *self, PyObject *args) {
    (void)self;
    const char *request;
    if (!PyArg_ParseTuple(args, "s", &request))
        return NULL;
    char *error = NULL;
    void *session;
    Py_BEGIN_ALLOW_THREADS session = tc_session_open(request, &error);
    Py_END_ALLOW_THREADS if (session == NULL) {
        PyErr_SetString(PyExc_RuntimeError, error ? error : "session allocation failed");
        tc_string_free(error);
        return NULL;
    }
    PyObject *capsule = PyCapsule_New(session, "rcfp.session", session_destroy);
    if (!capsule)
        tc_session_free(session);
    return capsule;
}
static PyObject *session_operation(PyObject *self, PyObject *args) {
    (void)self;
    const char *request;
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "Os", &capsule, &request))
        return NULL;
    void *session = PyCapsule_GetPointer(capsule, "rcfp.session");
    if (!session)
        return NULL;
    char *response;
    Py_BEGIN_ALLOW_THREADS response = tc_session_operation(session, request);
    Py_END_ALLOW_THREADS if (!response) return PyErr_NoMemory();
    PyObject *result = PyUnicode_FromString(response);
    tc_string_free(response);
    return result;
}

static PyObject *session_request_full(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule;
    const char *method, *url, *headers, *body;
    Py_ssize_t body_len;
    if (!PyArg_ParseTuple(args, "Osssy#:session_request_full", &capsule, &method, &url, &headers,
                          &body, &body_len))
        return NULL;
    void *session = PyCapsule_GetPointer(capsule, "rcfp.session");
    if (!session)
        return NULL;
    TcResponse *response;
    Py_BEGIN_ALLOW_THREADS response = tc_session_http_request_full(
        session, method, url, headers, (const unsigned char *)body, (size_t)body_len);
    Py_END_ALLOW_THREADS return response_to_python(response, 1);
}

typedef struct {
    int32_t data_type;
    size_t height, width;
    double *values;
    unsigned char *mask;
    char *error;
} TcRaster;
extern TcRaster *tc_session_raster(const void *, const char *);
extern void tc_raster_free(TcRaster *);

static PyObject *session_raster(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule;
    const char *request;
    if (!PyArg_ParseTuple(args, "Os", &capsule, &request))
        return NULL;
    void *session = PyCapsule_GetPointer(capsule, "rcfp.session");
    if (!session)
        return NULL;
    TcRaster *raster;
    Py_BEGIN_ALLOW_THREADS raster = tc_session_raster(session, request);
    Py_END_ALLOW_THREADS if (!raster) return PyErr_NoMemory();
    if (raster->error) {
        PyErr_SetString(PyExc_RuntimeError, raster->error);
        tc_raster_free(raster);
        return NULL;
    }
    Py_ssize_t count = (Py_ssize_t)(raster->height * raster->width);
    PyObject *values =
        PyBytes_FromStringAndSize((const char *)raster->values, count * sizeof(double));
    PyObject *mask = PyBytes_FromStringAndSize((const char *)raster->mask, count);
    PyObject *result = values && mask
                           ? Py_BuildValue("i(nn)OO", raster->data_type, (Py_ssize_t)raster->height,
                                           (Py_ssize_t)raster->width, values, mask)
                           : NULL;
    Py_XDECREF(values);
    Py_XDECREF(mask);
    tc_raster_free(raster);
    return result;
}

static PyMethodDef methods[] = {
    {"session_raster", session_raster, METH_VARARGS, "Read native raster sample and mask buffers."},
    {"session_open", session_open, METH_VARARGS, "Create an owned native session."},
    {"session_operation", session_operation, METH_VARARGS, "Execute on an owned session."},
    {"session_request_full", session_request_full, METH_VARARGS,
     "Request on an owned HTTP session."},
    {"data_operation", native_data_operation, METH_VARARGS, "Execute native data preparation."},
    {"request_full", native_request_full, METH_VARARGS, "Execute a request with headers and body."},
    {"request", native_request, METH_VARARGS, "Execute one native request."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_native", "RCFP native adapter.", -1, methods,
};

PyMODINIT_FUNC PyInit__native(void) { return PyModule_Create(&module); }
