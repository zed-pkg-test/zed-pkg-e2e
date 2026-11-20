// Deterministic stress tests; fixed seeds make failures reproducible.
// These are native host operations, not Atto guest/AOT test evidence.
#include <atto/ownership_v1.hpp>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>
#include <memory_resource>
using namespace atto::ownership_v1;

static std::uint32_t next(std::uint32_t& seed) {
  seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
  return seed;
}

static Graph make_graph(std::uint32_t seed, std::uint32_t count) {
  Graph graph{1, {}};
  for (std::uint32_t i = 1; i <= count; ++i) {
    std::vector<std::uint32_t> edges;
    // Always connected. Edges may repeat, share children, or form cycles.
    if (i < count) edges.push_back(i + 1);
    edges.push_back(1 + next(seed) % count);
    edges.push_back(1 + next(seed) % count);
    graph.nodes.push_back(Node{i, EdgeKind::plain,
                               {static_cast<std::uint8_t>(i)}, std::move(edges)});
  }
  return graph;
}

int main() {
  // Pin the safety limits at both the last admitted and first rejected size.
  for (auto count : {1u, 2u, 3u, 16u, 64u, 128u}) {
    for (auto seed : {1u, 17u, 0x9E3779B9u}) {
      UniqueGraph original(5, make_graph(seed, count));
      assert(original.valid());
      const auto edge_limit = count * 3 - 1;
      assert(original.valid(Limits{count, edge_limit, count}));
      assert(!original.valid(Limits{count - 1, edge_limit, count}));
      assert(!original.valid(Limits{count, edge_limit - 1, count}));
      assert(!original.valid(Limits{count, edge_limit, count - 1}));
      auto twin = original.deep_copy(); assert(twin);
      assert(twin->mutate([](Graph& g) {g.nodes.front().bytes[0] = 252;}));
      assert(original.read([](const Graph& g) {return g.nodes.front().bytes[0];}) == 1);
      assert(twin->read([](const Graph& g) {return g.nodes.front().bytes[0];}) == 252);
      auto frozen = std::move(original).freeze(); assert(frozen);
      const auto permit = frozen->permit();
      auto weak = frozen->weak();
      auto view = weak.upgrade(permit); assert(view);
      assert(view->read(permit, [](const Graph& g) {return g.nodes.size();}) == count);
      frozen.reset();
      assert(!weak.upgrade(permit));
      assert(!view->read(permit, [](const Graph& g) {return g.nodes.size();}));
    }
  }

  // Retire must remain idempotent and race-free after repeated weak claims.
  for (std::uint32_t round = 0; round < 32; ++round) {
    auto root = std::move(UniqueGraph(7, make_graph(round + 1, 16))).freeze();
    assert(root);
    const auto permit = root->permit();
    auto weak = root->weak();
    std::atomic<unsigned> good{0}, bad{0};
    std::vector<std::thread> threads;
    for (int i = 0; i < 4; ++i) {
      threads.emplace_back([&, weak] {
        for (int n = 0; n < 100; ++n) {
          if (auto owner = weak.upgrade(permit)) {
            auto read = owner->read(permit, [](const Graph& g) {return g.nodes.size();});
            if (read) {assert(*read == 16); ++good;}
          } else ++bad;
        }
      });
    }
    root->retire();
    for (auto& thread : threads) thread.join();
    assert(!weak.upgrade(permit));
    assert(good.load() + bad.load() <= 400);
  }

  // Nested pointers in value snapshots must be rejected at compile time.
  static_assert(!owned_snapshot_v<std::vector<int*>>);
  static_assert(!owned_snapshot_v<std::pmr::vector<int>>);
  static_assert(owned_snapshot_v<std::vector<int>>);
  static_assert(!owned_snapshot_v<std::pair<int, const Node*>>);
  struct Immovable {
    int member;
    explicit Immovable(int i): member(i) {}
    Immovable(Immovable&&) = delete;
    Immovable(const Immovable&) = delete;
  };
  static_assert(!std::is_constructible_v<Pinned<Immovable>, Immovable>);
  Pinned<Immovable> pin(std::in_place, 42);
  const auto* pointer = pin.get();
  Pinned<Immovable> new_handle(std::move(pin));
  assert(new_handle.get() == pointer);
  assert(new_handle.get()->member == 42);
}
