import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";
import router from "./router";
import { initTheme } from "./composables/useTheme";

initTheme();

const app = createApp(App);
app.use(router);
app.mount("#app");
