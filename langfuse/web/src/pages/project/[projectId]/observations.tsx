import { type GetServerSideProps } from "next";

export default function ObservationsPage() {
  return null;
}

export const getServerSideProps: GetServerSideProps = async (context) => {
  const projectIdParam = context.params?.projectId;
  const projectId = Array.isArray(projectIdParam)
    ? projectIdParam[0]
    : projectIdParam;

  if (!projectId) {
    return { notFound: true };
  }

  return {
    redirect: {
      destination: `/project/${projectId}/traces`,
      permanent: false,
    },
  };
};
