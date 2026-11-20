#include <atto/ownership_v1.hpp>
// Tests must remain active even when CMake is run in Release mode.
#ifdef NDEBUG
#error "ownership_v1 tests must be compiled without NDEBUG"
#endif
#include <cassert>
#include <span>
#include <memory_resource>
#include <atomic>
#include <thread>
#include <utility>
using namespace atto::ownership_v1;
static Graph cycle() {
  return {1, {{1, EdgeKind::plain, {1}, {2, 2}}, {2, EdgeKind::plain, {2}, {1}}}};
}
int main() {
  assert(checked_graph(cycle()));
  auto bad = cycle(); bad.nodes[1].kind = EdgeKind::foreign_pointer;
  assert(!checked_graph(bad));
  for (auto kind : {EdgeKind::external_mutable, EdgeKind::foreign_pointer,
                    EdgeKind::dynamic_unknown}) {
    auto unsafe = cycle(); unsafe.nodes[1].kind = kind;
    assert(!checked_graph(unsafe));
  }
  auto duplicate = cycle(); duplicate.nodes[1].id = 1;
  assert(!checked_graph(duplicate));
  auto absent = cycle(); absent.nodes[0].edges.push_back(999);
  assert(!checked_graph(absent));
  assert(!checked_graph(cycle(), Limits{10,1,10}));
  auto original = UniqueGraph(7, cycle());
  auto copy = original.deep_copy(); assert(copy && copy->read([](const Graph& g) { return g.nodes.size(); }));
  assert(copy->read([](const Graph& g) { return g.nodes[0].edges; }) == original.read([](const Graph& g) { return g.nodes[0].edges; }));
  assert(copy->read([](const Graph& g) { return reinterpret_cast<std::uintptr_t>(g.nodes[0].bytes.data()); }) != original.read([](const Graph& g) { return reinterpret_cast<std::uintptr_t>(g.nodes[0].bytes.data()); }));
  assert(copy->mutate([](Graph& g) { g.nodes[1].bytes[0] = 99; }));
  assert(original.read([](const Graph& g) { return g.nodes[1].bytes[0]; }) == 2);
  auto frozen = std::move(original).freeze(); assert(frozen);
  assert(!original.read([](const Graph& g) { return g.nodes.size(); }));
  auto permit = frozen->permit();
  auto shared = frozen->share();
  auto weak = frozen->weak();
  auto upgraded = weak.upgrade(permit); assert(upgraded);
  assert(upgraded->read(permit, [](const Graph& g) { return g.nodes.size(); }) == 2);
  auto second = std::move(*copy).freeze(); assert(second);
  assert(!weak.upgrade(second->permit())); // same domain != same generation
  frozen->retire();
  assert(!weak.upgrade(permit));
  assert(!shared.read(permit, [](const Graph& g) { return g.nodes.size(); }));
  // A weak observer must never resurrect a retired region, including when
  // upgrades race on other threads and strong read handles remain in flight.
  {
    auto root = std::move(UniqueGraph(31, cycle())).freeze(); assert(root);
    auto token = root->permit();
    auto observer = root->weak();
    std::atomic<bool> begin{false};
    std::vector<std::thread> readers;
    for (int i = 0; i < 4; ++i) {
      readers.emplace_back([observer, token, &begin] {
        while (!begin.load(std::memory_order_acquire)) std::this_thread::yield();
        for (int j = 0; j < 1000; ++j) {
          if (auto strong = observer.upgrade(token)) {
            auto value = strong->read(token, [](const Graph& g) { return g.nodes.size(); });
            if (value) assert(*value == 2);
          }
        }
      });
    }
    begin.store(true, std::memory_order_release);
    root->retire();
    for (auto& t : readers) t.join();
    assert(!observer.upgrade(token));
  }
  // Move-assignment must retire any previously owned region. Otherwise an
  // unrelated strong handle could continue reading after losing its owner.
  {
    auto first = std::move(UniqueGraph(7, cycle())).freeze(); assert(first);
    auto first_token = first->permit();
    auto first_weak = first->weak();
    auto replacement = std::move(UniqueGraph(7, cycle())).freeze(); assert(replacement);
    *first = std::move(*replacement);
    assert(!first_weak.upgrade(first_token));
  }
  // Cycle-heavy graphs have bounded traversal and independent nested copies.
  for (std::uint32_t n : {4u, 8u, 32u}) {
    Graph many{1, {}};
    for (std::uint32_t i = 1; i <= n; ++i)
      many.nodes.push_back({i, EdgeKind::plain, {static_cast<std::uint8_t>(i)},
                            {i == n ? 1u : i + 1}});
    UniqueGraph root(3, std::move(many));
    assert(root.valid());
    auto twin = root.deep_copy(); assert(twin);
    assert(twin->mutate([](Graph& g) { g.nodes[0].bytes[0] = 77; }));
    assert(root.read([](const Graph& g) { return g.nodes[0].bytes[0]; }) == 1);
  }
  // Direct and nested pointer-bearing callback snapshots must reject.
  static_assert(owned_snapshot_v<int>);
  static_assert(owned_snapshot_v<std::vector<std::uint32_t>>);
  static_assert(owned_snapshot_v<std::pair<int, std::vector<int>>>);
  static_assert(!owned_snapshot_v<const Node*>);
  static_assert(!owned_snapshot_v<std::vector<const Node*>>);
  static_assert(!owned_snapshot_v<std::span<const Node>>);
  // pmr::vector carries external memory_resource lifetime authority: reject.
  static_assert(!owned_snapshot_v<std::pmr::vector<int>>);
  static_assert(!owned_snapshot_v<std::pmr::vector<const Node*>>);
  static_assert(owned_snapshot_v<std::vector<std::vector<int>>>);
  static_assert(owned_snapshot_v<std::optional<std::vector<int>>>);
  struct Immovable {
    int value;
    explicit Immovable(int x) : value(x) {}
    Immovable(const Immovable&) = delete;
    Immovable(Immovable&&) = delete;
  };
  auto immutable_pin = Pinned<Immovable>(std::in_place, 84);
  const auto* stable_before = immutable_pin.get();
  auto moved_immutable_pin = std::move(immutable_pin);
  assert(moved_immutable_pin.get() == stable_before);
  assert(moved_immutable_pin.get()->value == 84);
  auto pin = [] {
    Pinned<std::vector<int>> handle(std::in_place, std::initializer_list<int>{42,7});
    auto address = handle.get();
    auto moved = std::move(handle);
    assert(address == moved.get());
    return moved;
  }();
  const auto address = pin.get();
  auto worker = std::thread([p = std::move(pin), address]() mutable {
    assert(p.get() == address); assert(p.get()->at(0) == 42);
  });
  worker.join();
  struct Tracked {
    std::atomic<int>* drops;
    Tracked(std::atomic<int>* p): drops(p) {}
    Tracked(Tracked&& other) noexcept : drops(std::exchange(other.drops, nullptr)) {}
    ~Tracked() { if (drops) drops->fetch_add(1); }
  };
  std::atomic<int> drops{0};
  {
    OwnedResource<Tracked> r{Tracked(&drops)};
    assert(r.alive()); r.drop_now(); assert(!r.alive());
    r.drop_now(); assert(drops.load() == 1);
  }
  assert(drops.load() == 1);
}
