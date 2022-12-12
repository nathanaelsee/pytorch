#pragma once

#include <c10/cuda/CUDAMacros.h>

#include <memory>
#include <mutex>
#include <string>
#include <vector>

#ifdef USE_CUDA
#define TORCH_USE_CUDA_DSA
#endif

/// Number of assertion failure messages we can store. If this is too small
/// threads will fail silently.
constexpr int C10_CUDA_DSA_ASSERTION_COUNT = 10;
constexpr int C10_CUDA_DSA_MAX_STR_LEN = 512;

namespace c10 {
namespace cuda {

/// Holds information about any device-side assertions that fail.
/// Held in managed memory and access by both the CPU and the GPU.
struct DeviceAssertionData {
  /// Stringification of the assertion
  char assertion_msg[C10_CUDA_DSA_MAX_STR_LEN];
  /// File the assertion was in
  char filename[C10_CUDA_DSA_MAX_STR_LEN];
  /// Name of the function the assertion was in
  char function_name[C10_CUDA_DSA_MAX_STR_LEN];
  /// Line number the assertion was at
  int line_number;
  /// Number uniquely identifying the kernel launch that triggered the assertion
  uint32_t caller;
  /// block_id of the thread that failed the assertion
  int32_t block_id[3];
  /// third_id of the thread that failed the assertion
  int32_t thread_id[3];
};

/// Used to hold assertions generated by the device
/// Held in managed memory and access by both the CPU and the GPU.
struct DeviceAssertionsData {
  /// Total number of assertions found; a subset of thse will be recorded
  /// in `assertions`
  int32_t assertion_count;
  /// An array of assertions that will be written to in a race-free manner
  DeviceAssertionData assertions[C10_CUDA_DSA_ASSERTION_COUNT];
};

/// Use to hold info about kernel launches so that we can run kernels
/// asynchronously and still associate launches with device-side
/// assertion failures
struct CUDAKernelLaunchInfo {
  /// Filename of the code where the kernel was launched from
  const char* launch_filename;
  /// Function from which the kernel was launched
  const char* launch_function;
  /// Line number of where the code was launched from
  uint32_t launch_linenum;
  /// Backtrace of where the kernel was launched from, only populated if
  /// CUDAKernelLaunchRegistry::gather_launch_stacktrace is True
  std::string launch_stacktrace;
  /// Kernel that was launched
  const char* kernel_name;
  /// Device the kernel was launched on
  int device;
  /// Stream the kernel was launched on
  int32_t stream;
  /// A number that uniquely identifies the kernel launch
  uint64_t generation_number;
};

/// Circular buffer used to hold information about kernel launches
/// this is later used to reconstruct how a device-side kernel assertion failure
/// occurred CUDAKernelLaunchRegistry is used as a singleton
class C10_CUDA_API CUDAKernelLaunchRegistry {
 private:
  /// Assume that this is the max number of kernel launches that might ever be
  /// enqueued across all streams on a single device
  static constexpr int max_kernel_launches = 1024;
  /// How many kernel launch infos we've inserted. Used to ensure that circular
  /// queue doesn't provide false information by always increasing, but also to
  /// mark where we are inserting into the queue
#ifdef TORCH_USE_CUDA_DSA
  uint64_t generation_number = 0;
#endif
  /// Shared mutex between writer and accessor to ensure multi-threaded safety.
  mutable std::mutex read_write_mutex;
  /// Used to ensure prevent race conditions in GPU memory allocation
  mutable std::mutex gpu_alloc_mutex;
  /// Pointer to managed memory keeping track of device-side assertions. There
  /// is one entry for each possible device the process might work with. Unused
  /// entries are nullptrs. We could also use an unordered_set here, but this
  /// vector design will be faster and the wasted memory is small since we
  /// expect the number of GPUs per node will always be small
  std::vector<
      std::unique_ptr<DeviceAssertionsData, void (*)(DeviceAssertionsData*)>>
      uvm_assertions;
  /// A single circular buffer holds information about every kernel launch the
  /// process makes across all devices.
  std::vector<CUDAKernelLaunchInfo> kernel_launches;
  bool check_env_for_enable_launch_stacktracing() const;
  bool check_env_for_dsa_enabled() const;

 public:
  CUDAKernelLaunchRegistry();
  /// Register a new kernel launch and obtain a generation number back to be
  /// passed to the kernel
  uint32_t insert(
      const char* launch_filename,
      const char* launch_function,
      const uint32_t launch_linenum,
      const char* kernel_name,
      const int32_t stream_id);
  /// Get copies of the kernel launch registry and each device's assertion
  /// failure buffer so they can be inspected without raising race conditions
  std::
      pair<std::vector<DeviceAssertionsData>, std::vector<CUDAKernelLaunchInfo>>
      snapshot() const;
  /// Get a pointer to the current device's assertion failure buffer. If no such
  /// buffer exists then one is created. This means that the first kernel launch
  /// made on each device will be slightly slower because memory allocations are
  /// required
  DeviceAssertionsData* get_uvm_assertions_ptr_for_current_device();
  /// Gets the global singleton of the registry
  static CUDAKernelLaunchRegistry& get_singleton_ref();
  /// If not all devices support DSA, we disable it
  const bool do_all_devices_support_managed_memory = false;
  /// Whether or not to gather stack traces when launching kernels
  bool gather_launch_stacktrace = false;
  /// Whether or not host-side DSA is enabled or disabled at run-time
  /// Device-side code cannot be adjusted at run-time
  bool enabled = false;
  /// Whether or not a device has indicated a failure
  bool has_failed() const;
  /// Since multiple mechanisms can enable/disable, we add a function that
  /// aggregates them
  bool is_enabled() const;
};

std::string c10_retrieve_device_side_assertion_info();

} // namespace cuda
} // namespace c10

// Each kernel launched with TORCH_DSA_KERNEL_LAUNCH
// requires the same input arguments. We introduce the following macro to
// standardize these.
#define TORCH_DSA_KERNEL_ARGS                             \
  c10::cuda::DeviceAssertionsData *const assertions_data, \
      uint32_t assertion_caller_id

// This macro can be used to pass the DSA arguments onward to another
// function
#define TORCH_DSA_KERNEL_ARGS_PASS assertions_data, assertion_caller_id
