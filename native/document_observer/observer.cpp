// Read-only openNURBS host glue. Rust consumes the framed observation; this module
// owns no document registry, geometry, history, or persistent native pointers.
// Compile ONLY against the pinned rhino3dm source and matching openNURBS headers.
#include "bindings.h"
#include <pybind11/conduit/pybind11_conduit_v1.h>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <vector>

namespace {
constexpr size_t max_batch = 128 * 1024 * 1024;
constexpr size_t max_object = 16 * 1024 * 1024;
constexpr unsigned max_rows = 100000;
constexpr size_t max_name = 4096;
using Bytes = std::vector<unsigned char>;

void append(Bytes& out, const void* data, size_t size) {
    if (size > max_batch - out.size())
        throw std::runtime_error("observation batch limit");
    if (size == 0) return;
    if (!data) throw std::runtime_error("null observation source");
    const auto* begin = static_cast<const unsigned char*>(data);
    out.insert(out.end(), begin, begin + size);
}

void u32(Bytes& out, uint32_t value) {
    const unsigned char data[] = {
        static_cast<unsigned char>(value), static_cast<unsigned char>(value >> 8),
        static_cast<unsigned char>(value >> 16), static_cast<unsigned char>(value >> 24)};
    append(out, data, sizeof(data));
}

void blob(Bytes& out, const void* data, size_t size) {
    if (size > std::numeric_limits<uint32_t>::max())
        throw std::runtime_error("observation field limit");
    u32(out, static_cast<uint32_t>(size));
    append(out, data, size);
}

class WitnessArchive : public ON_Write3dmBufferArchive {
public:
    WitnessArchive() : ON_Write3dmBufferArchive(0, max_object, 80, ON::Version()) {}
    std::vector<std::pair<size_t, size_t>> bookkeeping;
protected:
    size_t Internal_WriteOverride(size_t count, const void* data) override {
        ON_3DM_BIG_CHUNK chunk;
        const auto position = CurrentPosition();
        if (count == 8 && GetCurrentChunk(chunk) > 0 && chunk.m_bLongChunk
            && position + count == chunk.m_start_offset) {
            // EndWrite3dmChunk rewrites the length after writing its CRC. The
            // actual archive stack identifies these fields, including nested
            // geometry; do not pattern-match arbitrary geometry bytes.
            const auto crc = chunk.SizeofCRC();
            if (crc && chunk.m_end_offset >= crc)
                bookkeeping.emplace_back(static_cast<size_t>(chunk.m_end_offset - crc), static_cast<size_t>(crc));
            if (chunk.m_typecode == TCODE_OPENNURBS_CLASS_USERDATA_HEADER)
                bookkeeping.emplace_back(static_cast<size_t>(chunk.m_start_offset + 32), 4);
        }
        return ON_Write3dmBufferArchive::Internal_WriteOverride(count, data);
    }
};

void archive(Bytes& out, const ON_Object& object, bool raw) {
    // Userdata may request DeleteAfterWrite. Serialize a private native duplicate,
    // never the document object. Virtual duplication stays in the original runtime.
    std::unique_ptr<ON_Object> copy(object.Duplicate());
    if (!copy) throw std::runtime_error("native observation duplication failed");
    // Solidity and bounding-box queries lazily fill serialized Brep caches.
    // Evaluate those native queries on the private copy so a later read cannot
    // look like a document edit. Do not overwrite topology/orientation fields.
    if (auto* brep = dynamic_cast<ON_Brep*>(copy.get())) (void)brep->IsSolid();
    if (auto* brep = dynamic_cast<ON_Brep*>(copy.get())) (void)brep->BoundingBox();
    for (const auto* item = object.FirstUserData(); item; item = item->Next()) {
        if (item->IsUnknownUserData())
            throw std::runtime_error("unknown native userdata observation not implemented yet");
        auto* copied = copy->GetUserData(item->m_userdata_uuid);
        if (!copied) throw std::runtime_error("native userdata cannot be copied for observation");
        copied->m_userdata_copycount = item->m_userdata_copycount;
    }
    WitnessArchive buffer;
    if (!buffer.WriteObject(*copy))
        throw std::runtime_error("native observation serialization failed or exceeded limit");
    if (raw) {
        blob(out, buffer.Buffer(), buffer.SizeOfArchive());
    } else {
        // The buffer is owned solely by this local archive, never by the document.
        auto* canonical = static_cast<unsigned char*>(const_cast<void*>(buffer.Buffer()));
        const auto size = buffer.SizeOfArchive();
        for (const auto& field : buffer.bookkeeping) {
            if (field.first > size || field.second > size - field.first)
                throw std::runtime_error("invalid native archive bookkeeping boundary");
            std::fill(canonical + field.first, canonical + field.first + field.second, 0);
        }
        // This is a comparison witness, NOT an importable geometry archive. Copy
        // counters and redundant CRC words are not document state; all payload bytes
        // remain. The raw test interface verifies actual archive round trips separately.
        blob(out, canonical, size);
    }
}

PyObject* observe_impl(PyObject* obj, bool full, bool raw = false) {
    try {
        // The GIL stays held. The borrowed pointer never escapes this call and no
        // geometry or attributes are changed. The loader must additionally pin the
        // rhino3dm build: conduit ABI/type checking is not source-layout provenance.
        auto* bound = pybind11_conduit_v1::get_type_pointer_ephemeral<BND_ONXModel>(obj);
        if (!bound) {
            if (!PyErr_Occurred()) PyErr_SetString(PyExc_TypeError, "compatible File3dm required");
            return nullptr;
        }
        const ONX_Model* model = bound->m_model.get();
        if (!model) throw std::runtime_error("native model unavailable");
        if (model->ActiveAndDeletedComponentCount(ON_ModelComponent::Type::RenderLight) != 0)
            throw std::runtime_error("render-light observation not implemented yet");
        const unsigned count = model->ActiveComponentCount(ON_ModelComponent::Type::ModelGeometry);
        if (count > max_rows) throw std::runtime_error("observation row limit");
        Bytes output;
        const char* magic = raw ? "NWRAW01\0" : full ? "NWOBS01\0" : "NWIDX01\0";
        append(output, magic, 8);
        u32(output, count);
        ONX_ModelComponentIterator iterator(*model, ON_ModelComponent::Type::ModelGeometry);
        unsigned seen = 0;
        for (auto ref = iterator.FirstComponentReference(); !ref.IsEmpty(); ref = iterator.NextComponentReference()) {
            if (++seen > count) throw std::runtime_error("observation membership changed");
            // ON_ClassId::Cast compares per-library class registry identities.
            // C++ RTTI checks the actual pinned cross-module type instead.
            const auto* component = dynamic_cast<const ON_ModelGeometryComponent*>(ref.ModelComponent());
            if (!component) throw std::runtime_error("unsupported native model component");
            const auto* geometry = component->Geometry(nullptr);
            const auto* attributes = component->Attributes(nullptr);
            if (!geometry || !attributes) throw std::runtime_error("incomplete native object");
            char id[37] = {};
            ON_UuidToString(attributes->m_uuid, id);
            append(output, id, 36);
            const ON_String name(attributes->m_name);
            const auto name_length = static_cast<size_t>(name.Length());
            if (name_length > max_name) throw std::runtime_error("observation name limit");
            blob(output, name.Array(), name_length);
            if (full) {
                archive(output, *geometry, raw);
                archive(output, *attributes, raw);
            }
        }
        if (seen != count) throw std::runtime_error("observation membership changed");
        return PyBytes_FromStringAndSize(reinterpret_cast<const char*>(output.data()),
                                        static_cast<Py_ssize_t>(output.size()));
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    } catch (const std::exception& error) {
        PyErr_SetString(PyExc_RuntimeError, error.what());
        return nullptr;
    } catch (...) {
        PyErr_SetString(PyExc_RuntimeError, "unknown native observation failure");
        return nullptr;
    }
}

PyObject* observe(PyObject*, PyObject* obj) { return observe_impl(obj, true); }
PyObject* index(PyObject*, PyObject* obj) { return observe_impl(obj, false); }
PyObject* raw_observe(PyObject*, PyObject* obj) { return observe_impl(obj, true, true); }

PyObject* copy_attributes(PyObject*, PyObject* args) {
    PyObject *source_obj, *holder_obj;
    const char* id;
    if (!PyArg_ParseTuple(args, "OsO", &source_obj, &id, &holder_obj)) return nullptr;
    try {
        auto* source = pybind11_conduit_v1::get_type_pointer_ephemeral<BND_ONXModel>(source_obj);
        if (!source) return nullptr;
        auto* holder = pybind11_conduit_v1::get_type_pointer_ephemeral<BND_ONXModel>(holder_obj);
        if (!holder) return nullptr;
        if (!source->m_model || !holder->m_model || source->m_model == holder->m_model)
            throw std::runtime_error("a separate private attribute holder is required");
        if (holder->m_model->ActiveComponentCount(ON_ModelComponent::Type::ModelGeometry) >= max_rows)
            throw std::runtime_error("attribute holder limit");
        auto ref = source->m_model->ComponentFromId(ON_ModelComponent::Type::ModelGeometry, ON_UuidFromString(id));
        auto* component = dynamic_cast<const ON_ModelGeometryComponent*>(ref.ModelComponent());
        if (!component || !component->Attributes(nullptr)) throw std::runtime_error("source attributes missing");
        // BND_CommonObject RTTI is hidden by rhino3dm. Obtain a target wrapper via
        // its supported API instead. The holder's dummy points ONLY own attributes;
        // they are never document geometry, queried as shapes, or exported.
        auto table = py::reinterpret_borrow<py::object>(holder_obj).attr("Objects");
        auto source_table = py::reinterpret_borrow<py::object>(source_obj).attr("Objects");
        py::object source_attributes = source_table.attr("FindId")(std::string(id)).attr("Attributes");
        auto key = table.attr("AddPoint")(0.0, 0.0, 0.0, source_attributes);
        std::string target_id = py::str(key);
        auto target_ref = holder->m_model->ComponentFromId(ON_ModelComponent::Type::ModelGeometry, ON_UuidFromString(target_id.c_str()));
        auto* target = dynamic_cast<const ON_ModelGeometryComponent*>(target_ref.ModelComponent());
        if (!target || !target->Attributes(nullptr)) throw std::runtime_error("attribute holder creation failed");
        // The destination object was just created in a private holder, not a document.
        auto* target_attributes = const_cast<ON_3dmObjectAttributes*>(target->Attributes(nullptr));
        // Preserve userdata copied by the ORIGINAL binding. Cross-library ON class
        // registries cannot safely duplicate arbitrary userdata, even with matching
        // layouts. Moving the already-copied list needs no class-ID conversion.
        ON_3dmObjectAttributes saved_userdata;
        saved_userdata.MoveUserData(*target_attributes);
        *target_attributes = *component->Attributes(nullptr);
        target_attributes->PurgeUserData();
        target_attributes->MoveUserData(saved_userdata);
        for (const auto* item = component->Attributes(nullptr)->FirstUserData(); item; item = item->Next()) {
            if (auto* copied = target_attributes->GetUserData(item->m_userdata_uuid))
                copied->m_userdata_copycount = item->m_userdata_copycount;
        }
        py::object result = table.attr("FindId")(key).attr("Attributes");
        return result.release().ptr();
    } catch (py::error_already_set& error) {
        error.restore();
        return nullptr;
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    } catch (const std::exception& error) {
        PyErr_SetString(PyExc_RuntimeError, error.what());
        return nullptr;
    } catch (...) {
        PyErr_SetString(PyExc_RuntimeError, "native attribute copy failed");
        return nullptr;
    }
}

PyMethodDef methods[] = {
    {"raw_observe", raw_observe, METH_O, "Test-only raw native archives, not registry witnesses."},
    {"index", index, METH_O, "Read UUID/name index only; does not serialize geometry."},
    {"copy_attributes", copy_attributes, METH_VARARGS, "Copy complete attributes into a private native holder."},
    {"observe", observe, METH_O, "Read a bounded NWOBS01 host observation; does not certify registry authority."},
    {nullptr, nullptr, 0, nullptr}
};
PyModuleDef module = {PyModuleDef_HEAD_INIT, "_narwhal_document_observer", nullptr, -1, methods};
} // namespace

PyMODINIT_FUNC PyInit__narwhal_document_observer() {
    ON::Begin();
    return PyModule_Create(&module);
}
