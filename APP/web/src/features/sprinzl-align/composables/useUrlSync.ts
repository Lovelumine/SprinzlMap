import { nextTick, onBeforeUnmount, onMounted } from "vue";
import type { Ref } from "vue";
import type { SeqEntry } from "../model/types";
import { getSequenceFromSearch } from "../model/utils";

interface UseSprinzlUrlSyncDeps {
  seqList: Ref<SeqEntry[]>;
  running: Ref<boolean>;
  defaultAnticode: string;
  runAlign: () => Promise<void>;
}

export function useSprinzlUrlSync(deps: UseSprinzlUrlSyncDeps) {
  const { seqList, running, defaultAnticode, runAlign } = deps;

  async function setFromURLIfAny(autoRun: boolean = true) {
    const urlSeq = getSequenceFromSearch(window.location.search);
    if (!urlSeq) return;
    seqList.value = [{ name: "", seq: urlSeq, anticode: defaultAnticode }];
    if (autoRun) {
      await nextTick();
      if (!running.value) {
        void runAlign();
      }
    }
  }

  const onPopstate = () => {
    void setFromURLIfAny(true);
  };

  onMounted(() => {
    void setFromURLIfAny(true);
    window.addEventListener("popstate", onPopstate);
  });

  onBeforeUnmount(() => {
    window.removeEventListener("popstate", onPopstate);
  });

  return {
    setFromURLIfAny,
  };
}
