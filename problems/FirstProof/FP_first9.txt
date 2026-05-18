Let $n \geq 5$.  
Let $A^{(1)}, \ldots, A^{(n)} \in \mathbb{R}^{3 \times 4}$ be Zariski-generic.   
For $\alpha, \beta, \gamma, \delta \in [n]$, construct $Q^{(\alpha \beta \gamma \delta)} \in \mathbb{R}^{3 \times 3 \times 3 \times 3}$ so that its $(i, j, k, \ell)$ entry for $1 \leq i, j, k, \ell \leq 3$ is given by $Q^{(\alpha \beta \gamma \delta)}_{i j k \ell} = \det [A^{(\alpha)}(i, :); A^{(\beta)}(j, :); A^{(\gamma)}(k, :); A^{(\delta)}(\ell, :)]$.
Here $A(i, :)$ denotes the $i$th row of a matrix $A$, and semicolon denotes vertical concatenation. 
We are interested in algebraic relations on the set of tensors $\{Q^{(\alpha \beta \gamma \delta)} : \alpha, \beta, \gamma, \delta \in [n] \}$.

More precisely, does there exist a polynomial map $\mathbf{F}: \mathbb{R}^{81n^4} \rightarrow \mathbb{R}^N$ that satisfies the following three properties?
\smallskip
\begin{itemize}\setlength\itemsep{0.5em}
\item The map $\mathbf{F}$ does not depend on $A^{(1)}, \ldots A^{(n)}$. 
\item The degrees of the coordinate functions of $\mathbf{F}$ do not depend on $n$.
\item Let $\lambda \in \mathbb{R}^{n \times n \times n \times n}$ satisfy 
$\lambda_{\alpha \beta \gamma \delta} \neq 0$ for precisely $\alpha, \beta, \gamma, \delta \in [n]$ that are not identical.  Then $\mathbf{F}(\lambda_{\alpha \beta \gamma \delta} Q^{(\alpha \beta \gamma \delta)} : \alpha, \beta, \gamma, \delta \in [n]) = 0$ holds if and only if there exist $u, v, w, x \in (\mathbb{R}^*)^n$ such that $\lambda_{\alpha \beta \gamma \delta} = u_{\alpha} v_{\beta} w_{\gamma} x_{\delta}$ for all $\alpha, \beta, \gamma, \delta \in [n]$ that are not identical. 
\end{itemize}
