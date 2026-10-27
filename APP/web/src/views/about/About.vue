<template>
  <div class="site--main">
    <div class="container-fluid">
      <el-card shadow="hover" class="box-card glass-card contact-card">
        <h2>Contact&nbsp;Us</h2>

        <p class="info-text">
          SprinzlMap is actively maintained as a workspace for verifiable,
          nucleotide-level Sprinzl numbering of mature and near-mature tRNA
          sequences. Questions, feedback, and collaboration ideas are welcome.
        </p>

        <div class="contact-info">
          <p class="mail">
            <strong>Lingling&nbsp;Zheng:</strong>
            <el-link href="mailto:zhengll33@mail.sysu.edu.cn" target="_blank">
              zhengll33@mail.sysu.edu.cn
            </el-link>
            <br />
            RNA Information Center, State Key Laboratory for Biocontrol, Sun
            Yat-sen University, Guangzhou 510275, P. R. China
          </p>

          <p class="mail">
            <strong>Zhuo&nbsp;Ouyang:</strong>
            <el-link
              href="mailto:ouyzh25@mail2.sysu.edu.cn"
              target="_blank"
            >
              ouyzh25@mail2.sysu.edu.cn
            </el-link>
            <br />
            RNA Information Center, State Key Laboratory for Biocontrol, Sun
            Yat-sen University, Guangzhou 510275, P. R. China
          </p>
        </div>
      </el-card>

      <el-card shadow="hover" class="box-card glass-card tools-card">
        <h2>Our&nbsp;Tools</h2>

        <p class="info-text">
          Alongside SprinzlMap, we maintain several databases and software systems
          for RNA function, regulation, and experimental interpretation.
        </p>

        <div class="tools-grid">
          <el-card shadow="always" class="tool-card">
            <a href="https://bioinformaticsscience.cn/rmbase/" target="_blank">
              <el-image
                src="https://minio.lumoxuan.cn/ensure/about/logoRMBase3.png"
                fit="contain"
                alt="RMBase Logo"
              />
            </a>
            <h4>RMBase V3.0</h4>
            <p class="info-text">
              Deciphering the map of RNA modifications from epitranscriptome
              data (Xuan et al., Nucleic Acids Res 2018).
            </p>
          </el-card>

          <el-card shadow="always" class="tool-card">
            <a href="https://rnasysu.com/encori/" target="_blank">
              <el-image
                src="https://minio.lumoxuan.cn/ensure/about/starBase_logo.png"
                fit="contain"
                alt="ENCORI Logo"
              />
            </a>
            <h4>ENCORI</h4>
            <p class="info-text">
              A database for miRNA&ndash;mRNA / miRNA&ndash;ncRNA interaction
              maps (Li et al., Nucleic Acids Res 2014).
            </p>
          </el-card>

          <el-card shadow="always" class="tool-card">
            <a href="http://rna.sysu.edu.cn/chipbase" target="_blank">
              <el-image
                src="https://minio.lumoxuan.cn/ensure/about/ChIPBase Logo.png"
                fit="contain"
                alt="ChIPBase Logo"
              />
            </a>
            <h4>ChIPBase v3.0</h4>
            <p class="info-text">
              Transcriptional regulatory networks of ncRNAs and genes (Zhou et
              al., Nucleic Acids Res 2017).
            </p>
          </el-card>

          <el-card shadow="always" class="tool-card">
            <a href="https://trna.lumoxuan.cn/" target="_blank">
              <el-image
                src="https://minio.lumoxuan.cn/ensure/bot/logo.webp"
                fit="contain"
                alt="ENSURE Logo"
              />
            </a>
            <h4>ENSURE</h4>
            <p class="info-text">
              Encyclopedia of suppressor tRNA with an AI assistant (Ouyang
              et&nbsp;al., Nucleic Acids Res 2025,
              doi:10.1093/nar/gkaf1062).
            </p>
          </el-card>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.site--main {
  min-height: 100vh;
  padding: 40px 0;
  background: transparent;
  display: flex;
  justify-content: center;
}

.container-fluid {
  width: 92%;
  max-width: 1100px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.box-card {
  border-radius: 16px;
  overflow: hidden;
}

.glass-card {
  background: var(--surface-2);
  backdrop-filter: blur(12px) saturate(180%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid var(--border-soft);
  color: var(--text-strong);
  padding: 24px;
}

.contact-info .mail {
  margin-bottom: 12px;
  line-height: 1.5;
}

.mail a {
  color: var(--accent-strong);
  font-weight: 600;
  text-decoration: none;
}

.mail a:hover {
  text-decoration: underline;
}

.tools-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  justify-content: space-between;
  margin-top: 10px;
}

.tool-card {
  width: calc(33.333% - 60px);
  min-width: 240px;
  background: var(--surface-2);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px;
  text-align: center;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}

.tool-card:hover {
  transform: translateY(-5px);
  box-shadow: var(--shadow-soft);
}

.tool-card h4 {
  margin: 10px 0 6px;
  font-size: 15px;
  color: var(--text-strong);
}

.tool-card :deep(img) {
  max-height: 140px;
  object-fit: contain;
}

.info-text {
  font-size: 14px;
  color: var(--text-soft);
}

@media (max-width: 768px) {
  .tool-card {
    width: 100%;
  }
}
</style>
