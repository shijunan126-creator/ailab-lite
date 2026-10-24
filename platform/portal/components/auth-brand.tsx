// The left half of the login and sign-up pages.
export default function AuthBrand() {
  return (
    <aside className="auth-brand">
      <div className="brand">
        <span className="brand-mark">A</span>
        AILab
      </div>
      <div>
        <h2>说出想法，剩下的交给 AILab</h2>
        <p>从一个问题到一份能直接用的 PPT、表格或报告。它会搜索资料、整理思路、动手完成，成果自动存进你的资料库。</p>
      </div>
      <ul className="auth-points">
        <li>对话即可问答、搜索与写作</li>
        <li>工作模式直接交付可下载的文件</li>
        <li>项目、资料库与记忆，越用越懂你</li>
      </ul>
    </aside>
  );
}
