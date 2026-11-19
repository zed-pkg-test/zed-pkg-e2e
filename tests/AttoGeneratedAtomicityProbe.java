// A test of the real emitted Java actor class, not a replacement for AttoLang
// compiler/guest CI. Reflective inspection runs on JVM only (not Native Image).
import java.lang.foreign.MemorySegment;
import java.lang.foreign.ValueLayout;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

public final class AttoGeneratedAtomicityProbe {
    private static void require(boolean condition, String reason) {
        if (!condition) throw new AssertionError(reason);
    }

    private static Object field(Object target, String name) throws Exception {
        Field f = target instanceof Class<?> c ? c.getDeclaredField(name)
                                              : target.getClass().getDeclaredField(name);
        f.setAccessible(true);
        return f.get(target instanceof Class<?> ? null : target);
    }

    private static void mustThrow(Method send, Object actor, long message,
                                  Class<? extends Throwable> expected) throws Exception {
        try {
            send.invoke(actor, message);
            throw new AssertionError("invalid send unexpectedly committed");
        } catch (InvocationTargetException error) {
            require(expected.isInstance(error.getCause()),
                "wrong typed failure: " + error.getCause());
        }
    }

    public static void main(String[] args) throws Exception {
        require(args.length == 0, "no dynamic guest input allowed");
        Class<?> type = Class.forName("AttoGeneratedMain$Actor_0");
        Constructor<?> constructor = type.getDeclaredConstructor();
        constructor.setAccessible(true);
        Method send = type.getDeclaredMethod("send", long.class);
        send.setAccessible(true);
        Method close = type.getDeclaredMethod("close");
        close.setAccessible(true);
        Object actor = constructor.newInstance();
        Object neighbor = constructor.newInstance();
        try {
            MemorySegment state = (MemorySegment) field(actor, "state");
            MemorySegment neighborState = (MemorySegment) field(neighbor, "state");
            require(state.get(ValueLayout.JAVA_LONG, 0) == 0L, "new state not zero");
            require((long) send.invoke(actor, Long.MAX_VALUE) == Long.MAX_VALUE,
                "first send unexpected value");
            require(state.get(ValueLayout.JAVA_LONG, 0) == Long.MAX_VALUE,
                "first send not durably committed");
            Class<?> program = Class.forName("AttoGeneratedMain");
            require((long) field(program, "actorStateDigest") == Long.MAX_VALUE,
                "wrong committed digest");
            require((long) field(program, "sentMessages") == 1L,
                "wrong committed sent count");

            // The actor-state sum is valid, but digest*31 overflows.
            mustThrow(send, actor, -1L, ArithmeticException.class);
            require(state.get(ValueLayout.JAVA_LONG, 0) == Long.MAX_VALUE,
                "digest overflow partially changed actor state");
            require((long) field(program, "actorStateDigest") == Long.MAX_VALUE,
                "digest overflow partially changed global trace");
            require((long) field(program, "sentMessages") == 1L,
                "digest overflow partially changed global message counter");

            // The actor-state arithmetic itself overflows.
            mustThrow(send, actor, 1L, ArithmeticException.class);
            require(state.get(ValueLayout.JAVA_LONG, 0) == Long.MAX_VALUE,
                "state arithmetic overflow partially committed");
            require(neighborState.get(ValueLayout.JAVA_LONG, 0) == 0L,
                "failed actor send corrupted neighbor state");
            close.invoke(actor);
            mustThrow(send, actor, 0L, IllegalStateException.class);
            require((long) field(program, "sentMessages") == 1L,
                "use-after-close incorrectly counted a send");
            try {
                state.get(ValueLayout.JAVA_LONG, 0);
                throw new AssertionError("retired native arena still readable");
            } catch (IllegalStateException expected) {
                // The FFM arena's lifetime fence should be active.
            }
        } finally {
            close.invoke(neighbor);
        }
        System.out.println("ATTO_JVM_ATOMIC_SEND_ROLLBACK=PASS");
        System.out.println("ATTO_JVM_NATIVE_ARENA_CLOSED_FENCE=PASS");
        System.out.println("EVIDENCE_SCOPE=EMITTED_JAVA_JVM_PROBE_NOT_NATIVE_IMAGE_TENANT_SANDBOX");
    }
}
