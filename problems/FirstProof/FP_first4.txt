Let $p(x)$ and $q(x)$ be two monic polynomials of degree $n$:
\[
p(x) = \sum_{k=0}^n a_k x^{n-k} \quad \text{and} \quad q(x) = \sum_{k=0}^n
b_k x^{n-k}
\]
where $a_0 = b_0 = 1$. Define $p \boxplus_n q(x)$ to be the polynomial
\[
(p \boxplus_n q)(x) = \sum_{k=0}^n c_k x^{n-k}
\]
where the coefficients $c_k$ are given by the formula:
\[
c_k = \sum_{i+j=k} \frac{(n-i)! (n-j)!}{n! (n-k)!} a_i b_j
\]
for $k = 0, 1, \dots, n$.
For a monic polynomial $p(x)=\prod_{i\le n}(x- \lambda_i)$, define 
$$\Phi_n(p):=\sum_{i\le n}(\sum_{j\neq i} \frac1{\lambda_i-\lambda_j})^2$$ and $\Phi_n(p):=\infty$ if $p$ has a multiple root.
Is it true that if $p(x)$ and $q(x)$ are monic real-rooted polynomials of
degree $n$, then
$$\frac1{\Phi_n(p\boxplus_n q)} \ge \frac1{\Phi_n(p)}+\frac1{\Phi_n(q)}?$$
