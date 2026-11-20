#pragma once
// Native *host reference* operations, not Forge guest/IR ownership lowering.
// All dynamically reachable edges are local node IDs, never host pointers.
#include <atomic>
#include <string>
#include <initializer_list>
#include <cstdint>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>

namespace atto::ownership_v1 {
// A callback may only export value snapshots known not to contain nested
// pointers or references. Checking !is_pointer<T> alone permits span<T>,
// vector<T*> and other accidental lifetime escapes.
 // Even a vector<T> with a non-owning polymorphic/custom allocator may
 // point into a caller-owned arena. Only the default allocator is certified. This only protects
// trusted host-facing return values; captured pointers need separate review.
template<class T> struct IsOwnedSnapshot : std::bool_constant<
    std::is_arithmetic_v<T> || std::is_enum_v<T>> {};
template<> struct IsOwnedSnapshot<std::string> : std::true_type {};
template<class T, class Alloc>
struct IsOwnedSnapshot<std::vector<T, Alloc>> : std::bool_constant<
    IsOwnedSnapshot<T>::value && std::is_same_v<Alloc, std::allocator<T>>> {};
template<class T> struct IsOwnedSnapshot<std::optional<T>> : IsOwnedSnapshot<T> {};
template<class A, class B> struct IsOwnedSnapshot<std::pair<A,B>> :
    std::bool_constant<IsOwnedSnapshot<A>::value && IsOwnedSnapshot<B>::value> {};
template<class T> inline constexpr bool owned_snapshot_v =
    IsOwnedSnapshot<std::remove_cvref_t<T>>::value;
enum class EdgeKind { plain, external_mutable, foreign_pointer, dynamic_unknown };
struct Node {
  std::uint32_t id;
  EdgeKind kind;
  std::vector<std::uint8_t> bytes;
  std::vector<std::uint32_t> edges;
};
struct Graph {
  std::uint32_t root;
  std::vector<Node> nodes;
};
struct Limits { std::size_t nodes = 1024, edges = 4096, bytes = 1024 * 1024; };

// Validate all reachable nodes, including cycles, before any publication.
// Reject disconnected nodes as well: a detached unsafe payload is still owned.
inline bool checked_graph(const Graph& g, Limits max = {}) {
  if (!g.root || g.nodes.empty() || g.nodes.size() > max.nodes) return false;
  std::unordered_map<std::uint32_t, std::size_t> by_id;
  std::size_t edges = 0, bytes = 0;
  for (std::size_t i = 0; i < g.nodes.size(); ++i) {
    const auto& n = g.nodes[i];
    if (!n.id || n.kind != EdgeKind::plain || !by_id.emplace(n.id, i).second) return false;
    if (n.edges.size() > max.edges - edges || n.bytes.size() > max.bytes - bytes) return false;
    edges += n.edges.size(); bytes += n.bytes.size();
  }
  if (!by_id.contains(g.root)) return false;
  std::vector<std::uint32_t> todo {g.root};
  std::set<std::uint32_t> seen;
  while (!todo.empty()) {
    auto id = todo.back(); todo.pop_back();
    if (!seen.insert(id).second) continue;
    const auto pos = by_id.find(id);
    if (pos == by_id.end()) return false;
    for (auto child : g.nodes[pos->second].edges) {
      if (!by_id.contains(child)) return false;
      todo.push_back(child);
    }
    // Unique nodes are bounded; repeated edges/cycles are already bounded.
  }
  return seen.size() == g.nodes.size();
}

struct Region {
  const std::uint64_t domain, generation;
  const Graph graph;
  std::atomic<bool> retired {false};
  Region(std::uint64_t d, std::uint64_t n, Graph g)
      : domain(d), generation(n), graph(std::move(g)) {}
};

class Permit {
  std::uint64_t domain_, generation_;
  Permit(std::uint64_t domain, std::uint64_t generation)
      : domain_(domain), generation_(generation) {}
  friend class FrozenOwner;
  friend class FrozenShared;
  friend class FrozenWeak;
};

class FrozenShared {
  std::shared_ptr<Region> region_;
  explicit FrozenShared(std::shared_ptr<Region> r) : region_(std::move(r)) {}
  friend class FrozenOwner;
  friend class FrozenWeak;
public:
  // The immutable backing graph is never returned by mutable pointer.
  // A retirement concurrent with a started callback may allow that callback
  // to finish; shared_ptr keeps physical storage alive until it completes.
  template<class F>
  auto read(const Permit& p, F&& f) const
      -> std::optional<decltype(f(std::declval<const Graph&>()))> {
    using Result = decltype(f(std::declval<const Graph&>()));
    static_assert(owned_snapshot_v<Result> && !std::is_reference_v<Result>,
        "read callbacks must return owned scalar/vector snapshots, not pointer-bearing values");
    if (!region_ || region_->retired.load(std::memory_order_acquire) ||
        p.domain_ != region_->domain || p.generation_ != region_->generation) return std::nullopt;
    return f(region_->graph);
  }
};
class FrozenWeak {
  std::weak_ptr<Region> region_;
  std::uint64_t domain_, generation_;
  FrozenWeak(const std::shared_ptr<Region>& r)
      : region_(r), domain_(r->domain), generation_(r->generation) {}
  friend class FrozenOwner;
public:
  std::optional<FrozenShared> upgrade(const Permit& p) const {
    if (p.domain_ != domain_ || p.generation_ != generation_) return std::nullopt;
    auto strong = region_.lock(); // atomic strong ref claim; never a raw address
    if (!strong || strong->retired.load(std::memory_order_acquire) ||
        strong->domain != domain_ || strong->generation != generation_) return std::nullopt;
    return FrozenShared(std::move(strong));
  }
};
class FrozenOwner {
  std::shared_ptr<Region> region_;
  explicit FrozenOwner(std::shared_ptr<Region> r) : region_(std::move(r)) {}
  friend class UniqueGraph;
public:
  FrozenOwner(const FrozenOwner&) = delete;
  FrozenOwner& operator=(const FrozenOwner&) = delete;
  FrozenOwner(FrozenOwner&&) noexcept = default;
  FrozenOwner& operator=(FrozenOwner&& other) noexcept {
    if (this != &other) { retire(); region_ = std::move(other.region_); }
    return *this;
  }
  ~FrozenOwner() { retire(); }
  Permit permit() const {
    if (!region_) throw std::logic_error("moved frozen owner");
    return Permit(region_->domain, region_->generation);
  }
  FrozenShared share() const {
    if (!region_) throw std::logic_error("moved frozen owner");
    return FrozenShared(region_);
  }
  FrozenWeak weak() const {
    if (!region_) throw std::logic_error("moved frozen owner");
    return FrozenWeak(region_);
  }
  void retire() noexcept { if (region_) region_->retired.store(true, std::memory_order_release); }
};

class UniqueGraph {
  std::unique_ptr<Graph> graph_;
  std::uint64_t domain_;
public:
  UniqueGraph(std::uint64_t domain, Graph graph)
      : graph_(std::make_unique<Graph>(std::move(graph))), domain_(domain) {}
  UniqueGraph(const UniqueGraph&) = delete;
  UniqueGraph& operator=(const UniqueGraph&) = delete;
  UniqueGraph(UniqueGraph&&) noexcept = default;
  UniqueGraph& operator=(UniqueGraph&&) noexcept = default;
  bool valid(Limits limit = {}) const { return domain_ != 0 && graph_ && checked_graph(*graph_, limit); }
  // C++ value copying recursively duplicates the vectors; local IDs maintain
  // diamond edges and cycles without a single source-graph mutable alias.
  std::optional<UniqueGraph> deep_copy(Limits limit = {}) const {
    if (!valid(limit)) return std::nullopt;
    return UniqueGraph(domain_, *graph_);
  }
  // Scoped callbacks prevent accidentally returning a raw guest pointer
  // that could dangle when the unique owner is frozen or dropped.
  template<class F>
  auto read(F&& f) const -> std::optional<decltype(f(std::declval<const Graph&>()))> {
    using Result = decltype(f(std::declval<const Graph&>()));
    static_assert(owned_snapshot_v<Result> && !std::is_reference_v<Result>,
        "graph read callback must return an independently owned scalar/vector snapshot");
    if (!graph_) return std::nullopt;
    return f(*graph_);
  }
  template<class F>
  bool mutate(F&& f) {
    if (!graph_) return false;
    f(*graph_);
    return true;
  }
  // Frozen publication consumes unique storage; caller must use std::move.
  std::optional<FrozenOwner> freeze(Limits limit = {}) && {
    if (!valid(limit)) return std::nullopt;
    static std::atomic<std::uint64_t> next_generation {1};
    auto generation = next_generation.load(std::memory_order_relaxed);
    while (true) {
      if (generation == 0 || generation == UINT64_MAX) return std::nullopt;
      if (next_generation.compare_exchange_weak(generation, generation + 1,
            std::memory_order_relaxed, std::memory_order_relaxed)) break;
    }
    auto region = std::make_shared<Region>(domain_, generation, std::move(*graph_));
    graph_.reset();
    return FrozenOwner(std::move(region));
  }
};

// The pointee address remains stable when the owning handle moves, including
// across OS threads. No borrow across await or FFI capability is implied.
template<class T>
class Pinned {
  std::unique_ptr<T> data_;
public:
  // Construct IN PLACE: accepting an existing T by value would move an
  // already self-referential object before it becomes pinned.
  template<class... Args>
  explicit Pinned(std::in_place_t, Args&&... args)
      : data_(std::make_unique<T>(std::forward<Args>(args)...)) {}
  Pinned(const Pinned&) = delete;
  Pinned& operator=(const Pinned&) = delete;
  Pinned(Pinned&&) noexcept = default;
  Pinned& operator=(Pinned&&) noexcept = default;
  const T* get() const { return data_.get(); }
};

// Explicit RAII destruction; no double release through the owning handle.
// The source borrow checker must reject drop! during a live loan.
template<class T>
class OwnedResource {
  std::unique_ptr<T> data_;
public:
  explicit OwnedResource(T value) : data_(std::make_unique<T>(std::move(value))) {
    static_assert(std::is_nothrow_destructible_v<T>,
                  "drop! requires noexcept destruction until unwind ABI is proven");
  }
  OwnedResource(const OwnedResource&) = delete;
  OwnedResource& operator=(const OwnedResource&) = delete;
  OwnedResource(OwnedResource&&) noexcept = default;
  OwnedResource& operator=(OwnedResource&&) noexcept = default;
  void drop_now() noexcept { data_.reset(); }
  bool alive() const noexcept { return static_cast<bool>(data_); }
};
} // namespace atto::ownership_v1
