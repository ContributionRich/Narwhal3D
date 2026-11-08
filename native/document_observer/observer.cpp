// openNURBS host glue. Rust owns the document registry and single history.
// Optional qualified lifetime tokens own no geometry or OCCT shapes; their native
// destructors only flag releases. Pinned-wheel file-wrapper cleanup is opt-in.
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
#include <map>
#include <atomic>
#include <thread>
#include <set>
#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

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



// Native lease: no Python calls, geometry pointers or allocations on release.
struct LeaseEntry { std::string key; std::atomic<bool> released{false}; std::weak_ptr<struct Lease> weak; };
struct Lease {
    LeaseEntry* entry;
    explicit Lease(LeaseEntry* value):entry(value){}
    ~Lease() noexcept { entry->released.store(true,std::memory_order_release); }
};
struct LeaseState { std::map<std::string,std::unique_ptr<LeaseEntry>> entries; };
LeaseState& leases() { static auto* value=new LeaseState; return *value; }
const ON_UUID lease_uuid=ON_UuidFromString("ce3bc21d-7639-439b-8535-635f2c5b7d51");
class MasterLeaseData : public ON_UserData {
public:
    std::shared_ptr<Lease> lease;
    const ON_ClassId* host_userdata_class=nullptr;
    MasterLeaseData() { m_userdata_uuid=lease_uuid; m_userdata_copycount=1; m_application_uuid=ON_nil_uuid; }
    MasterLeaseData(const MasterLeaseData& other):ON_UserData(other),lease(other.lease),host_userdata_class(other.host_userdata_class){}
    // Unregistered, nonarchived subclass. The wheel's native CopyUserData must
    // recognize its OWN ON_UserData base; the observer's separate registry cannot.
    const ON_ClassId* ClassId() const override { return host_userdata_class; }
    ON_Object* Internal_DeepCopy() const override { return new MasterLeaseData(*this); }
    bool Archive() const override { return false; }
    bool Transform(const ON_Xform&) override { return true; }
};

ON_Geometry* exact_brep(PyObject* obj) {
    auto type=py::module_::import("rhino3dm").attr("Brep");
    if (reinterpret_cast<PyObject*>(Py_TYPE(obj))!=type.ptr())
        throw std::runtime_error("exact Brep required");
    auto* info=py::detail::get_type_info(Py_TYPE(obj));
    if (!info || !info->cpptype) throw std::runtime_error("registered native type unavailable");
    auto* pointer=pybind11_conduit_v1::get_raw_pointer_ephemeral(obj,info->cpptype);
    if (!pointer) throw py::error_already_set();
    auto* geometry=static_cast<BND_Brep*>(pointer)->GeometryPointer();
    if (!geometry || !dynamic_cast<const ON_Brep*>(geometry))
        throw std::runtime_error("native Brep unavailable");
    return const_cast<ON_Geometry*>(geometry);
}
void pin_address(const void* address) {
    static auto* pinned=new std::set<const void*>;
    if(pinned->count(address))return;

#ifdef _WIN32
    HMODULE module;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
        reinterpret_cast<LPCWSTR>(address),&module))throw std::runtime_error("cannot pin native ownership library");
#else
    Dl_info info{};
    if(!dladdr(address,&info) || !info.dli_fname ||
       !dlopen(info.dli_fname,RTLD_NOW|RTLD_NODELETE))throw std::runtime_error("cannot pin native ownership library");
#endif

    pinned->insert(address);
}
void require_main_thread() {
#ifdef _WIN32
    throw std::runtime_error("native master lifetime is NOT_QUALIFIED on Windows; retain strong ownership");
#endif
    auto ident=py::module_::import("threading").attr("main_thread")().attr("ident");
    if(PyLong_AsUnsignedLongLong(ident.ptr())!=PyThread_get_thread_ident())
        throw std::runtime_error("master lifetime operations require the main thread");
}
PyObject* lease_bind(PyObject*,PyObject* args) {
    PyObject* obj;const char* id;
    if(!PyArg_ParseTuple(args,"Os",&obj,&id))return nullptr;
    try {
        if(!id[0] || std::strlen(id)>128)throw std::runtime_error("invalid lease id");
        require_main_thread();
        auto* geometry=exact_brep(obj);
        auto data=std::make_unique<MasterLeaseData>();
        for(auto* item=geometry->FirstUserData();item && !data->host_userdata_class;item=item->Next()) {
            if(dynamic_cast<MasterLeaseData*>(item)) continue;
            for(auto* cls=item->ClassId();cls;cls=cls->BaseClass()) {
                if(std::strcmp(cls->ClassName(),"ON_UserData")==0) {
                    data->host_userdata_class=cls;break;
                }
            }
        }
        if(!data->host_userdata_class)throw std::runtime_error("host-native userdata base unavailable");
        pin_address(reinterpret_cast<const void*>(&pin_address));
        pin_address(data->host_userdata_class);
        auto& entries=leases().entries;
        auto found=entries.find(id);
        if(found!=entries.end()) {
            data->lease=found->second->weak.lock();
            if(!data->lease)throw std::runtime_error("released lease cannot be resurrected");
        } else {
            auto entry=std::make_unique<LeaseEntry>();entry->key=id;
            data->lease=std::make_shared<Lease>(entry.get());entry->weak=data->lease;
            entries.emplace(id,std::move(entry));
        }
        auto* old=geometry->GetUserData(lease_uuid);
        if(old && !dynamic_cast<MasterLeaseData*>(old))throw std::runtime_error("foreign ownership userdata");
        if(old && !geometry->DetachUserData(old))throw std::runtime_error("ownership detach refused");
        if(!geometry->AttachUserData(data.get())) {
            if(old)geometry->AttachUserData(old);
            throw std::runtime_error("ownership attach refused");
        }
        data.release();delete old;
        Py_RETURN_NONE;
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
PyObject* lease_count(PyObject*,PyObject* obj) {
    try {
        require_main_thread();
        auto* geometry=exact_brep(obj);
        auto* data=dynamic_cast<MasterLeaseData*>(geometry->GetUserData(lease_uuid));
        return PyLong_FromLong(data?data->lease.use_count():0);
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
PyObject* lease_drain(PyObject*,PyObject*) {
    try {
        require_main_thread();
        auto output=py::list();auto& entries=leases().entries;
        for(auto it=entries.begin();it!=entries.end();) {
            if(it->second->released.load(std::memory_order_acquire)) {
                output.append(py::str(it->first));it=entries.erase(it);
            }else ++it;
        }
        return output.release().ptr();
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
constexpr const char* capsule_name="Narwhal3D.MasterLease.v1";
void capsule_release(PyObject* capsule) noexcept {
    auto* held=static_cast<std::shared_ptr<Lease>*>(PyCapsule_GetPointer(capsule,capsule_name));
    if(held)delete held;
    else PyErr_Clear();
}
PyObject* lease_hold(PyObject*,PyObject* obj) {
    try {
        require_main_thread();
        auto* data=dynamic_cast<MasterLeaseData*>(exact_brep(obj)->GetUserData(lease_uuid));
        if(!data || !data->lease)throw std::runtime_error("native master ownership is missing");
        auto held=std::make_unique<std::shared_ptr<Lease>>(data->lease);
        auto* capsule=PyCapsule_New(held.get(),capsule_name,capsule_release);
        if(capsule)held.release();
        return capsule;
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}

constexpr const char* native_capsule_name="Narwhal3D.NativeMasterOwner.v1";
void native_capsule_release(PyObject* capsule) noexcept {
    auto* held=static_cast<std::unique_ptr<ON_Object>*>(PyCapsule_GetPointer(capsule,native_capsule_name));
    if(held)delete held;else PyErr_Clear();
}
PyObject* lease_hold_native(PyObject*,PyObject* obj) {
    try {
        require_main_thread();auto* geometry=exact_brep(obj);
        if(!dynamic_cast<MasterLeaseData*>(geometry->GetUserData(lease_uuid)))throw std::runtime_error("native master ownership is missing");
        auto held=std::make_unique<std::unique_ptr<ON_Object>>(geometry->Duplicate());
        if(!*held || !dynamic_cast<MasterLeaseData*>((*held)->GetUserData(lease_uuid)))throw std::runtime_error("native ownership did not survive original-runtime duplicate");
        auto* capsule=PyCapsule_New(held.get(),native_capsule_name,native_capsule_release);
        if(capsule)held.release();return capsule;
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
PyObject* lease_thread_release(PyObject*,PyObject* capsule) {
    try {
        require_main_thread();
        if(PyCapsule_IsValid(capsule,native_capsule_name)) {
            auto* held=static_cast<std::unique_ptr<ON_Object>*>(PyCapsule_GetPointer(capsule,native_capsule_name));
            std::thread worker([value=std::move(*held)]() mutable {value.reset();});
            {py::gil_scoped_release release;worker.join();}
            Py_RETURN_NONE;
        }
        auto* held=static_cast<std::shared_ptr<Lease>*>(PyCapsule_GetPointer(capsule,capsule_name));
        if(!held)return nullptr;
        auto owned=std::move(*held);
        std::thread worker([value=std::move(owned)]() mutable { value.reset(); });
        {py::gil_scoped_release release;worker.join();}
        Py_RETURN_NONE;
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}

// The pinned wheel allocates both child BND wrappers in FileObjectFromCompRef,
// returns them reference_internal, but BND_FileObject has no owning destructor.
// Claim the otherwise-leaked wrappers only after the Python parent is dead.
struct FileOwner {
    BND_FileObject* identity;
    BND_GeometryBase* geometry;
    BND_3dmObjectAttributes* attributes;
    PyObject* weak;
};
std::set<BND_FileObject*>& claimed_files(){static auto* p=new std::set<BND_FileObject*>;return *p;}
constexpr const char* file_owner_name="Narwhal3D.FileObjectOwner.v1";
void file_owner_release(PyObject* capsule) noexcept {
    auto* owner=static_cast<FileOwner*>(PyCapsule_GetPointer(capsule,file_owner_name));
    if(!owner){PyErr_Clear();return;}
    // Manual/detached finalizer invocation while its parent is alive must never
    // free a child still reachable through the parent. Conservatively leak then.
    PyObject* parent=nullptr;
    const int alive=PyWeakref_GetRef(owner->weak,&parent);
    if(alive==0){delete owner->geometry;delete owner->attributes;}
    else if(alive<0)PyErr_Clear();
    Py_XDECREF(parent);
    claimed_files().erase(owner->identity);
    Py_DECREF(owner->weak);delete owner;
}
PyObject* file_owner(PyObject*,PyObject* obj) {
    try {
        require_main_thread();
        auto type=py::module_::import("rhino3dm").attr("File3dmObject");
        if(reinterpret_cast<PyObject*>(Py_TYPE(obj))!=type.ptr())throw std::runtime_error("exact File3dmObject required");
        auto* bound=pybind11_conduit_v1::get_type_pointer_ephemeral<BND_FileObject>(obj);
        if(!bound)return nullptr;
        if(claimed_files().count(bound))Py_RETURN_NONE;
        if(!bound->m_geometry || !bound->m_attributes)throw std::runtime_error("native file-object children unavailable");
        pin_address(reinterpret_cast<const void*>(&pin_address));
        pin_address(bound->m_geometry->GeometryPointer()->ClassId());
        auto* weak=PyWeakref_NewRef(obj,nullptr);if(!weak)return nullptr;
        auto owner=std::make_unique<FileOwner>(FileOwner{bound,bound->m_geometry,bound->m_attributes,weak});
        auto* raw=PyCapsule_New(owner.get(),file_owner_name,file_owner_release);
        if(!raw){Py_DECREF(weak);return nullptr;}
        owner.release();py::object capsule=py::reinterpret_steal<py::object>(raw);
        claimed_files().insert(bound);
        auto finalizer=py::module_::import("weakref").attr("finalize")(py::handle(obj),py::module_::import("builtins").attr("id"),capsule);
        finalizer.attr("atexit")=false;
        Py_RETURN_NONE;
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}

PyObject* lease_key(PyObject*,PyObject* obj) {
    try {
        require_main_thread();
        auto* data=dynamic_cast<MasterLeaseData*>(exact_brep(obj)->GetUserData(lease_uuid));
        if(!data)Py_RETURN_NONE;
        return PyUnicode_FromString(data->lease->entry->key.c_str());
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
PyObject* lease_unbind(PyObject*,PyObject* obj) {
    try {
        require_main_thread();auto* geometry=exact_brep(obj);
        auto* data=geometry->GetUserData(lease_uuid);
        if(data && !dynamic_cast<MasterLeaseData*>(data))throw std::runtime_error("foreign master lifetime userdata");
        if(data){if(!geometry->DetachUserData(data))throw std::runtime_error("master lifetime detach refused");delete data;}
        Py_RETURN_NONE;
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
PyObject* lease_hold_key(PyObject*,PyObject* obj) {
    try {
        require_main_thread();auto key=py::cast<std::string>(py::handle(obj));
        auto found=leases().entries.find(key);if(found==leases().entries.end())Py_RETURN_NONE;
        auto lease=found->second->weak.lock();if(!lease)Py_RETURN_NONE;
        auto held=std::make_unique<std::shared_ptr<Lease>>(std::move(lease));
        auto* capsule=PyCapsule_New(held.get(),capsule_name,capsule_release);
        if(capsule)held.release();return capsule;
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
PyObject* lease_stats(PyObject*,PyObject*) {
    try {
        require_main_thread();size_t pending=0;long owners=0;
        for(const auto& entry:leases().entries){pending+=entry.second->released.load(std::memory_order_acquire);owners+=entry.second->weak.use_count();}
        return Py_BuildValue("{sK,sK,sK,sL}","entries",static_cast<unsigned long long>(leases().entries.size()),"pending",static_cast<unsigned long long>(pending),"file_owners",static_cast<unsigned long long>(claimed_files().size()),"owners",static_cast<long long>(owners));
    } catch(py::error_already_set& error){error.restore();return nullptr;}
      catch(const std::exception& error){PyErr_SetString(PyExc_RuntimeError,error.what());return nullptr;}
}
PyObject* master_lifetime_abi(PyObject*,PyObject*) {
#ifdef _WIN32
    return PyLong_FromLong(0);
#else
    return PyLong_FromLong(1);
#endif
}
PyMethodDef methods[] = {
    {"master_lifetime_abi", master_lifetime_abi, METH_NOARGS, "Master lifetime ABI; zero on unqualified platforms."},
    {"lease_key", lease_key, METH_O, "Native ownership identity, independent of persisted tags."},
    {"lease_unbind", lease_unbind, METH_O, "Detach only this geometry ownership token."},
    {"lease_hold_key", lease_hold_key, METH_O, "Explicit owner for opaque registry rollback payload."},
    {"lease_stats", lease_stats, METH_NOARGS, "Native lifetime inventory; no geometry serialization."},
    {"lease_hold_native", lease_hold_native, METH_O, "Qualified lifetime: explicit native-copy owner; tests original-runtime destruction."},
    {"file_owner", file_owner, METH_O, "Qualified lifetime: own pinned wheel file-object wrappers until parent GC."},
    {"lease_hold", lease_hold, METH_O, "Qualified lifetime: explicit owner for serialized payload."},
    {"lease_thread_release", lease_thread_release, METH_O, "Qualified lifetime: release on native thread without GIL."},
    {"lease_bind", lease_bind, METH_VARARGS, "Qualified lifetime: bind native lease."},
    {"lease_count", lease_count, METH_O, "Qualified lifetime: native ownership count."},
    {"lease_drain", lease_drain, METH_NOARGS, "Qualified lifetime: drain native releases."},
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
