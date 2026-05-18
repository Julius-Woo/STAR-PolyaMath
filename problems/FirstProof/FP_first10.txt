Given a $d$-way tensor $\mathcal{T} \in \mathbb{R}^{n_1 \times n_2 \times \cdots \times n_d}$ 
such that the data is unaligned (meaning the tensor $\mathcal{T}$ has missing entries),
we consider the problem of computing a CP decomposition of rank $r$ where some modes are infinite-dimensional and constrained to be in a Reproducing Kernel Hilbert Space (RKHS). 
We want to solve this using an alternating optimization approach, and our question is focused on the mode-$k$ subproblem for an infinite-dimensional mode. 
For the subproblem, then CP factor matrices 
$A_1, \dots, A_{k-1}, A_{k+1}, \dots, A_d$ are fixed, and we are solving for $A_k$.

Our notation is as follows.
Let $N = \prod_i n_i$ denote the product of all sizes.
Let $n \equiv n_k$ be the size of mode $k$, let
$M = \prod_{i\neq k} n_i$ be the product of all dimensions except $k$, and assume $n \ll M$.
Since the data are unaligned, this means only a subset of $\mathcal{T}$'s entries are observed, and we let $q \ll N$ denote the number of observed entries.
We let $T \in \mathbb{R}^{n \times M}$ denote the mode-$k$ unfolding of the tensor $\mathcal{T}$ with all missing entries set to zero.
The $\vecop$ operations creates a vector from a matrix by stacking its columns,
and we let $S \in \mathbb{R}^{N \times q}$ denote the selection matrix (a subset of the $N \times N$ identity matrix) such that $S^T \vecop(T)$ selects the $q$ known entries of the tensor $\mathcal{T}$ from the vectorization of its mode-$k$ unfolding.
We let $Z = A_d \odot \cdots \odot A_{k+1} \odot A_{k-1} \odot \cdots \odot A_1 \in \mathbb{R}^{M \times r}$ be the Khatri-Rao product of the factor matrices corresponding to all modes except mode $k$.
We let $B = TZ$ denote the MTTKRP of the tensor $\mathcal{T}$ and Khatri-Rao product $Z$.

We assume $A_k = KW$ where
$K \in \mathbb{R}^{n \times n}$ denotes the psd RKHS kernel matrix for mode $k$.
The matrix $W$ of size $n \times r$ is the unknown for which we must solve. 
The system to be solved is
\begin{equation} 
	\left[
    (Z \otimes K)^T S
    S^T (Z \otimes K)
    + \lambda (I_r \otimes K) 
  \right] \vecop(W)
	= (I_r \otimes K) 
	\vecop( B ). \nonumber 
\end{equation}
Here, $I_r$ denotes the $r \times r$ identity matrix.
This is a system of size $nr \times nr$
Using a standard linear solver costs $O(n^3 r^3)$, 
and explicitly forming the matrix is an additional expense.

Explain how an iterative preconditioned conjugate gradient linear solver can be used to solve this problem more efficiently. Explain the method and choice of preconditioner. Explain in detail how the matrix-vector products are computed and why this works. Provide complexity analysis. 
We assume $n,r < q \ll N$. Avoid any computation of order $N$.
