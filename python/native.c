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
extern TcResponse *tc_http_request_full(const char *, const char *, const char *, const char *, const unsigned char *, size_t);
extern void tc_response_free(TcResponse *);
extern char *tc_copy_dataset(const char *, const char *, const char *);
extern void tc_string_free(char *);
extern char *tc_data_operation(const char *);

static PyObject *request_common(PyObject *self, PyObject *args, int full) {
    (void)self;
    const char *database;
    const char *method;
    const char *url;
    const char *headers = "{}";
    const char *request_body = "";
    Py_ssize_t request_body_len = 0;
    if (full) {
        if (!PyArg_ParseTuple(args, "ssssy#:request_full", &database, &method, &url, &headers, &request_body, &request_body_len)) return NULL;
    } else if (!PyArg_ParseTuple(args, "sss:request", &database, &method, &url)) return NULL;
    TcResponse *response;
    Py_BEGIN_ALLOW_THREADS
    response = tc_http_request_full(database, method, url, headers, (const unsigned char *)request_body, (size_t)request_body_len);
    Py_END_ALLOW_THREADS
    if (response == NULL) {
        return PyErr_NoMemory();
    }
    if (response->error != NULL) {
        PyErr_SetString(PyExc_RuntimeError, response->error);
        tc_response_free(response);
        return NULL;
    }
    PyObject *result = PyTuple_New(full ? 5 : 4);
    PyObject *status = PyLong_FromLong(response->status);
    PyObject *content_type = PyUnicode_FromString(response->content_type);
    PyObject *body = PyBytes_FromStringAndSize(
        (const char *)response->body, (Py_ssize_t)response->body_len
    );
    PyObject *cache = PyBool_FromLong(response->cache);
    if (result == NULL || status == NULL || content_type == NULL || body == NULL || cache == NULL) {
        Py_XDECREF(result);
        Py_XDECREF(status);
        Py_XDECREF(content_type);
        Py_XDECREF(body);
        Py_XDECREF(cache);
        tc_response_free(response);
        return NULL;
    }
    PyTuple_SET_ITEM(result, 0, status);
    PyTuple_SET_ITEM(result, 1, content_type);
    PyTuple_SET_ITEM(result, 2, body);
    PyTuple_SET_ITEM(result, 3, cache);
    if (full) {
        PyObject *headers = PyUnicode_FromString(response->headers_json);
        if (headers == NULL) {Py_DECREF(result); tc_response_free(response); return NULL;}
        PyTuple_SET_ITEM(result, 4, headers);
    }
    tc_response_free(response);
    return result;
}

static PyObject *native_request(PyObject *self, PyObject *args) { return request_common(self, args, 0); }
static PyObject *native_request_full(PyObject *self, PyObject *args) { return request_common(self, args, 1); }

static PyObject *native_copy_dataset(PyObject *self, PyObject *args) {
    (void)self;
    const char *source;
    const char *destination;
    const char *keys_json;
    if (!PyArg_ParseTuple(
            args, "sss:copy_dataset", &source, &destination, &keys_json
        )) {
        return NULL;
    }
    char *error = tc_copy_dataset(source, destination, keys_json);
    if (error != NULL) {
        PyErr_SetString(PyExc_RuntimeError, error);
        tc_string_free(error);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *native_data_operation(PyObject *self, PyObject *args) {
    (void)self;
    const char *request;
    if (!PyArg_ParseTuple(args, "s:data_operation", &request)) return NULL;
    char *response;
    Py_BEGIN_ALLOW_THREADS
    response = tc_data_operation(request);
    Py_END_ALLOW_THREADS
    if (response == NULL) return PyErr_NoMemory();
    PyObject *result = PyUnicode_FromString(response);
    tc_string_free(response);
    return result;
}

extern void *tc_session_open(const char *, char **);
extern char *tc_session_operation(const void *, const char *);
extern void tc_session_free(void *);

static void session_destroy(PyObject *capsule) {
    void *session = PyCapsule_GetPointer(capsule, "rcfp.session");
    if (session != NULL) tc_session_free(session);
}
static PyObject *session_open(PyObject *self, PyObject *args) {
    (void)self;
    const char *request;
    if (!PyArg_ParseTuple(args, "s", &request)) return NULL;
    char *error = NULL;
    void *session;
    Py_BEGIN_ALLOW_THREADS
    session = tc_session_open(request, &error);
    Py_END_ALLOW_THREADS
    if (session == NULL) {
        PyErr_SetString(PyExc_RuntimeError, error ? error : "session allocation failed");
        tc_string_free(error);
        return NULL;
    }
    PyObject *capsule = PyCapsule_New(session, "rcfp.session", session_destroy);
    if (!capsule) tc_session_free(session);
    return capsule;
}
static PyObject *session_operation(PyObject *self, PyObject *args) {
    (void)self;
    const char *request;
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "Os", &capsule, &request)) return NULL;
    void *session = PyCapsule_GetPointer(capsule, "rcfp.session");
    if (!session) return NULL;
    char *response;
    Py_BEGIN_ALLOW_THREADS
    response = tc_session_operation(session, request);
    Py_END_ALLOW_THREADS
    if (!response) return PyErr_NoMemory();
    PyObject *result = PyUnicode_FromString(response);
    tc_string_free(response);
    return result;
}

static PyMethodDef methods[] = {
    {"session_open", session_open, METH_VARARGS, "Create an owned native session."},
    {"session_operation", session_operation, METH_VARARGS, "Execute on an owned session."},
    {"data_operation", native_data_operation, METH_VARARGS, "Execute native data preparation."},
    {"request_full", native_request_full, METH_VARARGS, "Execute a request with headers and body."},
    {"request", native_request, METH_VARARGS, "Execute one native request."},
    {"copy_dataset", native_copy_dataset, METH_VARARGS, "Copy one legacy dataset."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_native",
    "Terracotta P03 native adapter.",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__native(void) { return PyModule_Create(&module); }
