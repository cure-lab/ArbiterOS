-- AlterTable
ALTER TABLE "error_analyses"
ADD COLUMN     "error_type" TEXT,
ADD COLUMN     "error_type_description" TEXT,
ADD COLUMN     "error_type_why" TEXT,
ADD COLUMN     "error_type_confidence" DOUBLE PRECISION,
ADD COLUMN     "error_type_from_list" BOOLEAN;

-- CreateIndex
CREATE INDEX "error_analyses_project_id_error_type_idx" ON "error_analyses"("project_id", "error_type");

